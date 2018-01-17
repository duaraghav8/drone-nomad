import boto3
import time
import json
import subprocess
from sys import stdout
from os import path, getenv
import decimal
from .config import build_config, NOMAD_BIN_PATH

in_local_mode = True if getenv('LOCAL_MODE') == 'true' else False
logger = None


def _get_client(service, role, region, session_name, resource=None):
    sts = boto3.client('sts', region_name=region)
    creds = sts.assume_role(RoleArn=role, RoleSessionName='{}-{}'.format(session_name, service)).get('Credentials')

    kind = boto3.resource if resource else boto3.client
    return kind(service,
                aws_access_key_id=creds.get('AccessKeyId'),
                aws_secret_access_key=creds.get('SecretAccessKey'),
                aws_session_token=creds.get('SessionToken'),
                region_name=region)


def _load_job_spec(job):
    subp = subprocess.Popen([NOMAD_BIN_PATH, 'run', '--output', job + '.nomad'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stdout, stderr = subp.communicate()
    if subp.returncode != 0:
        raise Exception(stderr)

    return json.loads(stdout)


def _match_cond(cond, data):
    matcher = cond.replace('@cond(', '').rstrip(')').split(' ')
    if len(matcher) != 3:
        raise Exception('Invalid syntax for condition "{}"'.format(cond))

    if matcher[0] not in data.keys():
        return False

    expected = matcher[2]
    present = data[matcher[0]]
    op = matcher[1]

    if op == '=':
        return expected == present
    elif op == '!=':
        return expected != present
    else:
        raise Exception('Condition operation "{}" is not recognized'.format(op))


_supported_types = (dict, str, int, float, complex, bool, bytes, type(None))


def _merge(base, extras):
    for key in extras:
        if isinstance(extras[key], list):
            if key not in base or base[key] is None:
                base[key] = extras[key]
            elif isinstance(base[key], list):
                base[key].extend(extras[key])
            else:
                raise Exception(
                    'Conflicting values at "{}", list type can override only empty values or lists'.format(key))
            continue

        if not isinstance(extras[key], _supported_types):
            raise Exception('Overrides must only contain scalar or dictionary values. Unsupported type {} on {}'.format(
                str(type(extras[key])), key))

        nest = False
        ref_key = key
        if key.endswith('.*'):
            ref_key = key[:-2]
            nest = True

        if ref_key not in base:
            if ref_key.startswith('@cond'):
                base = _merge(base, extras[key]) if _match_cond(key, base) else base
            else:
                base[ref_key] = extras[key]
        elif isinstance(base[ref_key], dict) and isinstance(extras[key], dict):
            base[ref_key] = _merge(base[ref_key], extras[key])
        elif isinstance(base[ref_key], list) and isinstance(extras[key], dict) and nest:
            base[ref_key] = [_merge(each, extras[key]) for each in base[ref_key]]
        else:
            base[ref_key] = extras[key]

    return base


def _replace_decimals(obj):
    if isinstance(obj, list):
        for i in range(len(obj)):
            obj[i] = _replace_decimals(obj[i])
        return obj
    elif isinstance(obj, dict):
        for k in obj.keys():
            obj[k] = _replace_decimals(obj[k])
        return obj
    elif isinstance(obj, decimal.Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj


def _merge_specs(base, overrides):
    if overrides is None:
        return base

    base['Job'] = _merge(base['Job'], _replace_decimals(overrides))
    return base


def _update_task_container(task, tag):
    if task['Driver'] != 'docker':
        return task

    meta = task.get('Meta', {})
    meta = dict() if meta is None else meta
    if meta.get('version_pinned') in [True, "true", "1", 1]:
        return task

    uri, _ = task['Config']['image'].split(':')
    task['Config']['image'] = '{}:{}'.format(uri, tag)

    return task


def _add_service_tags(task, tag):
    if 'Services' not in task or task['Services'] is None:
        return task

    for service in task['Services']:
        if service['Tags'] is None:
            service['Tags'] = []

        new_tags = ['{}-{}'.format(t, tag) for t in service['Tags']]
        service['Tags'].extend(new_tags)

    return task


def _add_task_revision(task, revision):
    if 'Meta' not in task or task['Meta'] is None:
        task['Meta'] = dict()

    task['Meta']['REVISION'] = revision
    return task


def _update_versions(spec, tag, task_name):
    target_tasks = task_name.split(',')
    spec_copy = spec.copy()
    for gid, group in enumerate(spec['Job']['TaskGroups']):
        for tid, each in enumerate(group['Tasks']):
            if each['Name'] in target_tasks or task_name == 'all':
                task = spec_copy['Job']['TaskGroups'][gid]['Tasks'][tid]
                task = _update_task_container(task, tag)
                task = _add_service_tags(task, tag)
                task = _add_task_revision(task, tag)
                spec_copy['Job']['TaskGroups'][gid]['Tasks'][tid] = task

    return spec_copy


def _process_job_overrides(*, dynamo, base_spec, env, task, tag, dc):
    job_name = base_spec['Job']['ID']
    data = dynamo.get_item(Key={'job': job_name,
                                'environment': env})

    spec = _merge_specs(base_spec, overrides=data.get('Item', {}).get('overrides'))
    if dc is not None:
        spec['Job']['Region'] = dc[0]
        spec['Job']['Datacenters'] = dc[1].split(',')

    return _update_versions(spec, tag, task)


def _print_plan(plan):
    print('Job: "{}"'.format(plan.get('Diff').get('ID')))
    for group in plan.get('Diff').get('TaskGroups'):
        print('Task Group "{}"'.format(group.get('Name')))
        for k, v in group.get('Updates').items():
            print('  {}: {}'.format(k, v))

        for task in group.get('Tasks'):
            if task.get('Type') == 'None':
                continue

            ann = task.get('Annotations')
            ann = '(' + ' & '.join(ann) + ')' if ann is not None else ''
            print('  {} task "{}" {}'.format(task.get('Type'), task.get('Name'), ann))
            for field in task.get('Fields') or []:
                ann = field.get('Annotations')
                ann = '(' + ' & '.join(ann) + ')' if ann is not None else ''
                print('    {} field {}: "{}" -> "{}" {}'.format(field['Type'],
                                                                field['Name'],
                                                                field['Old'],
                                                                field['New'],
                                                                ann))


def _plan_deployment(client, spec):
    diff = client(spec=spec['Job'], action='plan')
    failures = diff.get('FailedTGAllocs') or dict()
    if failures.keys():
        print('Failed to place allocations: ' + json.dumps(failures, indent=2))
        raise Exception('Task plan failed')

    _print_plan(diff)
    return diff.get('JobModifyIndex')


def _queue_job(client, spec, modification_index):
    result = client(spec=spec, action='run', index=modification_index)
    if result.get('EvalID') == "":
        return None

    evaluation = client(action='get_eval', evaluation_id=result.get('EvalID'))
    return evaluation.get('DeploymentID')


def _ready_to_promote(deployment):
    for name, group in deployment.get('TaskGroups').items():
        desired_total = group.get('DesiredTotal')
        desired_canaries = group.get('DesiredCanaries')
        placed_canaries = group.get('PlacedCanaries')
        placed_allocs = group.get('PlacedAllocs')
        healthy = group.get('HealthyAllocs')
        unhealthy = group.get('UnhealthyAllocs')

        placed_canaries = len(placed_canaries) if placed_canaries is not None else 0

        logger('Validating task {}'.format(name))
        if placed_canaries != desired_canaries:
            logger('Placed canaries != desired canaries')
            return False

        if unhealthy > 0:
            logger('Unhealthy allocations')
            return False

        if healthy < desired_total:
            logger('healthy < desired')
            return False

        if placed_canaries + placed_allocs < desired_total:
            logger('placed canaries + placed allocs < desired')
            return False

    logger('Allocations are ready for promotion')
    return True


def _allocations_placed(client, deployment_id):
    deployment = client(action='get_deployment', deployment_id=deployment_id)
    status = deployment.get('Status')
    if status is None:
        raise Exception('Failed to retrieve deployment status')

    if status == 'successful':
        return True

    if status == 'cancelled' or status == 'failed':
        raise Exception('Deployment failed with status "{}"'.format(status))

    if status == 'running':
        return _ready_to_promote(deployment)

    return False


def _promote_canaries(client, deployment_id):
    client(action='promote', deployment_id=deployment_id)


def _get_lambda_client(func, iam_role_arn, region, session_name):
    def _sync_client(**kwargs):
        from .lambda_handler import _actions
        return _actions[kwargs.get('action')](kwargs)

    def _lambda(client):
        def _client_wrapper(**kwargs):
            response = client.invoke(FunctionName=func, Payload=json.dumps(kwargs).encode())
            if response['StatusCode'] != 200:
                raise Exception('Lambda invocation failure: {}'.format(response['Payload'].read()))

            result = json.load(response['Payload'])
            if 'FunctionError' in response and response['FunctionError'] in ['Handled', 'Unhandled']:
                raise Exception('Lambda invocation failure: {}'.format(json.dumps(result, indent=2)))


            logger('Received payload from lambda')
            logger(json.dumps(result, indent=2))

            return result

        return _client_wrapper

    if in_local_mode:
        return _sync_client
    else:
        return _lambda(_get_client('lambda', iam_role_arn, region, session_name))


def _get_dynamodb_table(table_name, iam_role, region, session_prefix):
    class DumbTable(object):
        def __init__(self, path_prefix):
            self._path_prefix = path_prefix

        def get_item(self, **kwargs):
            job_name = kwargs['Key']['job']
            env = kwargs['Key']['environment']
            patch_file = path.join(self._path_prefix, '{}_{}.json'.format(env, job_name))
            if not path.exists(patch_file):
                return dict()

            with open(patch_file) as f:
                return dict(Item=json.load(f))

    if in_local_mode:
        return DumbTable(table_name)
    else:
        client = _get_client('dynamodb', iam_role, region, session_prefix, resource=True)
        return client.Table(table_name)


def _get_promotion_cb(client, spec, task_name, tag):
    job_name = spec.get('Job').get('Name')
    task_names = task_name.split(',')

    ns = dict()
    for group in spec.get('Job').get('TaskGroups'):
        for task in group.get('Tasks'):
            if task.get('Name') in task_names or task_name == 'all':
                ns['_config/services/{}/{}/{}/active_tag'.format(job_name, group.get('Name'), task.get('Name'))] = tag

    def _cb():
        for k, v in ns.items():
            result = client(action='put_kv', key=k, value=v)
            print('put_kv "{}" = "{}" -> {}'.format(k, v, result.get('result')))

    return _cb


def _on_placements_ready(client, deployment_id, cb):
    while not _allocations_placed(client, deployment_id):
        print('Deployment is still running, waiting for allocations to be placed...')
        time.sleep(10)
        continue

    return cb()


def place_allocations(target_env, target_job, target_task, container_tag, lambda_func, dynamodb_table,
                      commit_id, build_number, account_number, local_account, region, ci_role, dc, only_plan):
    session_name_prefix = 'drone-{}-{}'.format(commit_id[:8], build_number)

    if not path.exists('{}.nomad'.format(target_job)):
        raise Exception('Unknown target job {}. Expecting file "{}.nomad" to exist'.format(target_job, target_job))

    local_arn = 'arn:aws:iam::{}:role/{}'.format(local_account, ci_role)
    target_arn = 'arn:aws:iam::{}:role/{}'.format(account_number, ci_role)
    lambda_client = _get_lambda_client(lambda_func, target_arn, region, session_name_prefix)
    dynamodb_table = _get_dynamodb_table(dynamodb_table, local_arn, region, session_name_prefix)

    job_spec = _load_job_spec(target_job)
    job_spec = _process_job_overrides(dynamo=dynamodb_table,
                                      base_spec=job_spec,
                                      env=target_env,
                                      tag=container_tag,
                                      task=target_task,
                                      dc=dc)

    logger('Final job specification')
    logger(json.dumps(job_spec, indent=2))

    modification_index = _plan_deployment(lambda_client, job_spec)
    _update_active_ref = _get_promotion_cb(lambda_client, job_spec, target_task, container_tag)

    if not only_plan:
        deployment_id = _queue_job(lambda_client, job_spec.get('Job'), modification_index)
        if deployment_id is not None and deployment_id != "":
            _on_placements_ready(lambda_client, deployment_id, _update_active_ref)
            print('All allocations are in place, you can promote the deployment now')
        else:
            print('Deployment successful')


def _latest_deployment_id(client, job_id):
    deployment = client(action='get_last_deployment', job_id=job_id)
    return deployment['ID']


def promote_allocations(target_job, lambda_func, account_number, region, ci_role, commit_id, build_number):
    session_name_prefix = 'drone-{}-{}'.format(commit_id[:8], build_number)
    if not path.exists('{}.nomad'.format(target_job)):
        raise Exception('Unknown target job {}. Expecting file "{}.nomad" to exist'.format(target_job, target_job))

    target_arn = 'arn:aws:iam::{}:role/{}'.format(account_number, ci_role)
    lambda_client = _get_lambda_client(lambda_func, target_arn, region, session_name_prefix)

    job_spec = _load_job_spec(target_job)
    deployment_id = _latest_deployment_id(lambda_client, job_spec.get('Job').get('ID'))

    def _promote():
        return _promote_canaries(lambda_client, deployment_id)

    _on_placements_ready(lambda_client, deployment_id, _promote)


def get_logger(verbose):
    def _l(msg):
        if verbose:
            stdout.write(' +' + str(msg))
            stdout.flush()

    return _l


_actions = {
    'create': place_allocations,
    'promote': promote_allocations,
}

if __name__ == '__main__':
    import os

    config = build_config()

    logger = get_logger(config['verbose'])
    logger('Configuration:')
    [logger('{} = {}'.format(k, v)) for k, v in config.items()]
    logger('Environment')
    [logger('{} = {}'.format(k, v)) for k, v in os.environ.items()]
    del config['verbose']

    action = config.get('action')
    del config['action']

    _actions[action](**config)
