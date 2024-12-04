import boto3
import json
import urllib.parse

sqs_client = boto3.client('sqs')
queue_url = 'https://sqs.us-east-1.amazonaws.com/xxxxxxxxxxxx/imat-datasets'

def lambda_handler(event, context):
    for record in event['Records']:
        s3 = record['s3']
        bucket_name = s3['bucket']['name']
        object_key = urllib.parse.unquote_plus(s3['object']['key'])

        message_body = {
            'bucket_name': bucket_name,
            'object_key': object_key
        }

        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body)
        )

    return {
        'statusCode': 200,
        'body': json.dumps('Messages sent to SQS')
    }
