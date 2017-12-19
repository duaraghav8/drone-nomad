import requests
import boto3
import random
from os import getenv

in_local_mode = True if getenv('LOCAL_MODE') == 'true' else False
server_tag = getenv('TAG_NAME', 'role')
tag_value = getenv('TAG_VALUE', 'nomad-server')


def _get_random_server():
    ec2_client = boto3.client("ec2")
    response = ec2_client.describe_instances(Filters=[{
        "Name": "tag:{}".format(server_tag),
        "Values": [tag_value]
    }])

    if response["ResponseMetadata"]["HTTPStatusCode"] is not 200:
        raise Exception("Failed to fetch Nomad servers on 'tag:{} == {}'. Error: {}".format(server_tag,
                                                                                            tag_value,
                                                                                            str(response)))

    reservation = random.choice(response['Reservations'])
    instance = random.choice(reservation['Instances'])

    return instance['PrivateIpAddress']


def _url(uri):
    host = '127.0.0.1' if in_local_mode else _get_random_server()
    return 'http://{}:4646/v1{}'.format(host, uri)


def _make_request(method, uri, json=None):
    response = requests.request(method, uri, json=json)
    if response.status_code > 299:
        raise Exception('API call to {} failed with status {}. {}'.format(uri, response.status_code, response.text))

    return response.json()


def _plan(event):
    job_id = event.get('spec').get('ID')
    return _make_request('post', _url('/job/{}/plan'.format(job_id)), json=dict(Job=event.get('spec'), Diff=True))


def _run(event):
    return _make_request('post', _url('/jobs'),
                         json=dict(Job=event.get('spec'), EnforceIndex=True, JobModifyIndex=event.get('index')))


def _get_evaluation(event):
    return _make_request('get', _url('/evaluation/{}'.format(event.get('evaluation_id'))))


def _get_deployment(event):
    return _make_request('get', _url('/deployment/{}'.format(event.get('deployment_id'))))


def _promote(event):
    return _make_request('post', _url('/deployment/promote/{}'.format(event.get('deployment_id'))),
                         json=dict(DeploymentID=event.get('deployment_id'), All=True))


_actions = {
    'plan': _plan,
    'run': _run,
    'get_eval': _get_evaluation,
    'get_deployment': _get_deployment,
    'promote': _promote,
}


def lambda_handler(event, context):
    return _actions[event['action']](event)
