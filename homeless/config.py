from os import getenv, path
from boto import utils

NOMAD_BIN_PATH = getenv('NOMAD_BIN_PATH', '/usr/bin/nomad')

_required = {'DRONE_DEPLOY_TO',
             'target_task',
             'PLUGIN_LAMBDA_FUNC',
             'PLUGIN_DYNAMODB_TABLE',
             'DRONE_COMMIT',
             'DRONE_BUILD_NUMBER'}


def _get_account_number():
    dep_target = getenv('DRONE_DEPLOY_TO')
    if dep_target is None:
        raise Exception('Deployment is only supported when the build is deployment source')
    else:
        varname = 'account_number_{}'.format(dep_target).upper()
        return getenv(varname)


def _get_self_region():
    data = utils.get_instance_identity()
    return data['document']['region']


def _only_plan():
    return getenv('plan') == 'true'


def _get_datacenters():
    d = getenv('destination')
    if d is None:
        return None

    if ':' not in d:
        raise Exception('Malformed value provided for destination. Expected format "region:datacenter", found {}'.format(d))

    return d.split(':')


def _get_tag():
    commit = getenv('DRONE_COMMIT')
    return commit[:8]


def _is_debug():
    return getenv('PLUGIN_DEBUG') == 'true' or getenv('debug') == 'true'


_optional = {
    'ACCOUNT_NUMBER': _get_account_number,
    'PLUGIN_REGION': _get_self_region,
    'PLUGIN_CI_ROLE': 'ci',
    'only_plan': _only_plan,
    'dc': _get_datacenters,
    'container_tag': _get_tag,
    'target_job': 'jobspec',
    'verbose': _is_debug
}

_normal_names = {
    'ACCOUNT_NUMBER': 'account_number',
    'PLUGIN_REGION': 'region',
    'PLUGIN_CI_ROLE': 'ci_role',
    'DRONE_DEPLOY_TO': 'target_env',
    'PLUGIN_LAMBDA_FUNC': 'lambda_func',
    'PLUGIN_DYNAMODB_TABLE': 'dynamodb_table',
    'DRONE_COMMIT': 'commit_id',
    'DRONE_BUILD_NUMBER': 'build_number'
}


def build_config():
    config = dict()
    for each in _required:
        if getenv(each) is None:
            raise Exception('Required parameter {} is not set'.format(each))
        if each in _normal_names.keys():
            config[_normal_names[each]] = getenv(each)
        else:
            config[each] = getenv(each)

    for k, v in _optional.items():
        if getenv(k) is not None:
            v = getenv(k)
        else:
            if callable(v):
                v = v()

        if k in _normal_names.keys():
            config[_normal_names[k]] = v
        else:
            config[k] = v

    return config


