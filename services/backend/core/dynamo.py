import boto3

_REGION = "ap-southeast-1"


def get_dynamo_resource():
    return boto3.resource("dynamodb", region_name=_REGION)
