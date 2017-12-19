# drone-nomad

A Drone plugin to facilitate Nomad job deployment.
This is an experiment, do not use it for anything you consider important.

## Components

The plugin relies on a Lambda function (see `./homeless/lambda_handler.py`) to proxy calls to
Nomad API.

It can also make changes in the jobspec before triggering the deployment so it is possible
to have a partial job specification in git repository and rest of the information in DynamoDB.

### Partial Job Specification

To work with partial job specifications the plugin tries to fetch the delta from DynamoDb table.

The DynamoDb table must have a primary key on (`job`, `environment`) where `job` is the Job ID and
`environment` is the target environment which will receive the delta. The patch is expected to be
on attribute `overrides`.

Overrides are written in a specific format which provides control over list of objects
and can target objects based on conditions. Generally the delta is applied as recursive
dictionary merge but two special formats can be used to control merge behaviour:

 - `Attribute.*`: Apply the patch on every element in the list
 - `@cond(attr [=|!=] val)`: Apply patch only to the element which satisfies the condition. Only `=` and `!=` operators are supported.

Given a JSON object:

    {
        "Name": "user",
        "Projects": [
            {
                "Name": "drone-nomad",
                "Language": "python"
            },
            {
                "Name": "caddy",
                "Language": "go"
            }
        ]
    }

Following override will add a new attribute `Owner` to every project:

    {
        "Projects.*": {
            "Owner": "user"
        }
    }

And following attribute will add the same attribute but only to the project
with name `drone-nomad`

    {
        "Projects.*": {
            "@cond(Name = drone-nomad)": {
                "Owner": "user"
            }
        }
    }


## Plugin Configuration

Following environment variables can be used to configure the plugin:

| Name | Description | Default Value | Required |
|:-----|:-------------|:--------------:|:-------:|
| ACCOUNT_NUMBER | AWS Account number to use | Account of the EC2 machine | No |
| DRONE_BUILD_NUMBER | Build number | None | Yes |
| DRONE_COMMIT | Commit hash to work with | None | Yes |
| DRONE_DEPLOY_TO | Name of target environment | None | Yes |
| NOMAD_BIN_PATH | Full path to Nomad binary | `assets/nomad` | No |
| PLUGIN_CI_ROLE | IAM role name (not arn) to assume in ACCOUNT_NUMBER | ci | No |
| PLUGIN_DYNAMODB_TABLE | Name of the DynamoDB table to work with | None | Yes |
| PLUGIN_LAMBDA_FUNC | Name of the lambda function to work with | None | Yes |
| PLUGIN_REGION | Name of AWS region | Region of the EC2 machine | No |
| container_tag | Container tag which will be deployed | First 8 characters of DRONE_COMMIT | No |
| dc | Nomad region and datacenter in `region:datacenter` format | As specified in Job spec | No |
| only_plan | set to `true` to print plan and exit | `false` | No |
| target_job | Name of the job file to deploy | `jobspec.nomad` | No |
| target_task | Name of the task to change | None | Yes |


## Example

``` yaml
pipeline:
  deploy:
    image: kayako/drone-nomad
    ci_role: ci-server
    dynamodb_table: drone_nomad_overrides
    lambda_func: DeployFunc
    region: us-east-1
    secrets: [ account_number_production, account_number_staging, account_number_develop ]
```

Trigger a deployment for this pipeline using drone CLI or github deployment API with remaining parameters:

``` sh
# with drone cli
drone deploy --param 'container_tag=abcd' \
             --param 'dc=nvirginia:pod_1' \
             --param 'only_plan=true' \
             --param 'target_job=project' \
             --param 'target_task=task_name' \
             project 1 staging
```

``` sh
# with github deployment API
curl -s -X POST -H 'Accept: application/vnd.github.ant-man-preview+json' \
    -d '{
      "ref": "staging",
      "environment": "staging",
      "description": "Deploying to staging using github API",
      "payload": "{\"container_tag\":\"abcd\",\"dc\":\"nvirginia:pod_1\",\"only_plan\":true,\"target_job\":\"project\",\"target_task\":\"task_name\"}"
    }' https://api.github.com/repos/octocat/octocat/deployments
```
