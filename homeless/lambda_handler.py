import requests
import boto3
import random
from os import getenv

in_local_mode = True if getenv('LOCAL_MODE') == 'true' else False
nomad_server_tag = getenv('NOMAD_TAG_NAME', 'nomad_class')
nomad_tag_value = getenv('NOMAD_TAG_VALUE', 'nomad-server')

consul_server_tag = getenv('CONSUL_TAG_NAME', 'role')
consul_tag_value = getenv('CONSUL_TAG_VALUE', 'consul-server')


def _get_random_server(tag_name, tag_value):
    ec2_client = boto3.client('ec2')
    response = ec2_client.describe_instances(Filters=[{
        'Name': 'tag:{}'.format(tag_name),
        'Values': [tag_value]
    }])

    if response['ResponseMetadata']['HTTPStatusCode'] is not 200:
        raise Exception('Failed to fetch Nomad servers on "tag:{} == {}". Error: {}'.format(tag_name,
                                                                                            tag_value,
                                                                                            str(response)))

    reservation = random.choice(response['Reservations'])
    instance = random.choice(reservation['Instances'])

    return instance['PrivateIpAddress']


def _server_for_kind(kind):
    if kind == 'nomad':
        return _get_random_server(nomad_server_tag, nomad_tag_value)
    elif kind == 'consul':
        return _get_random_server(consul_server_tag, consul_tag_value)
    else:
        raise Exception('Unrecognized server kind {}'.format(kind))


def _url(uri, kind):
    port = '4646' if kind == 'nomad' else '8500'
    host = '127.0.0.1' if in_local_mode else _server_for_kind(kind)
    return 'http://{}:{}/v1{}'.format(host, port, uri)


def _nomad_url(uri):
    return _url(uri, 'nomad')


def _consul_url(uri):
    return _url(uri, 'consul')


def _make_request(method, uri, as_json, **kwargs):
    response = requests.request(method, uri, **kwargs)
    if response.status_code > 299:
        raise Exception('API call to {} failed with status {}. {}'.format(uri, response.status_code, response.text))

    return response.json() if as_json else response.text


def _plan(event):
    job_id = event.get('spec').get('ID')
    return _make_request('post', _nomad_url('/job/{}/plan'.format(job_id)), as_json=True,
                         json=dict(Job=event.get('spec'), Diff=True))


def _run(event):
    return _make_request('post', _nomad_url('/jobs'), as_json=True,
                         json=dict(Job=event.get('spec'), EnforceIndex=True, JobModifyIndex=event.get('index')))


def _get_evaluation(event):
    return _make_request('get', _nomad_url('/evaluation/{}'.format(event.get('evaluation_id'))), as_json=True)


def _get_deployment(event):
    return _make_request('get', _nomad_url('/deployment/{}'.format(event.get('deployment_id'))), as_json=True)


def _get_last_deployment(event):
    return _make_request('get', _nomad_url('/job/{}/deployment'.format(event.get('job_id'))), as_json=True)


def _promote(event):
    return _make_request('post', _nomad_url('/deployment/promote/{}'.format(event.get('deployment_id'))), as_json=True,
                         json=dict(DeploymentID=event.get('deployment_id'), All=True))


def _put_kv(event):
    return {
        'result': _make_request('put', _consul_url('/kv/{}'.format(event.get('key'))),
                                data=event.get('value'), as_json=False)
    }


_actions = {
    'plan': _plan,
    'run': _run,
    'get_eval': _get_evaluation,
    'get_deployment': _get_deployment,
    'promote': _promote,
    'put_kv': _put_kv,
    'get_last_deployment': _get_last_deployment,
}


def lambda_handler(event, context):
    return _actions[event['action']](event)
