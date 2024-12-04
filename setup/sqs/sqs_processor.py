import boto3
import hashlib
import urllib.parse
import os
import logging
import json
import time
from datetime import datetime
from botocore.exceptions import ClientError
from files import *
from data_sources import *
from datasets import *
from dashboard import *

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture all log levels

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super(DateTimeEncoder, self).default(obj)

# Initialize AWS clients outside the Lambda handler for reuse
s3_client = boto3.client('s3')
quicksight_client = boto3.client('quicksight')
dynamodb = boto3.client('dynamodb')

# DynamoDB table to track datasets
dynamodb_table = 'imat-dashboard-datasets'

def get_table_maps(aws_account_id, s3_client, file_name, table_map_bucket, datasource_id):
    try:
        physical_table_map = get_s3_object(s3_client, table_map_bucket, f"{file_name}/physical_table_map.json")
        logical_table_map = get_s3_object(s3_client, table_map_bucket, f"{file_name}/logical_table_map.json")
        return json.loads(physical_table_map), json.loads(logical_table_map)
    except Exception as e:
        logger.error(f"Error getting table maps: {e}")
        raise

def get_s3_object(s3_client, bucket, key):
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response['Body'].read().decode('utf-8')
    except ClientError as e:
        logger.error(f"Error getting S3 object {key} from bucket {bucket}: {e}")
        raise

def describe_data_source(quicksight_client, aws_account_id, datasource_id):
    try:
        response = quicksight_client.describe_data_source(
            AwsAccountId=aws_account_id,
            DataSourceId=datasource_id
        )
        return response
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            return None
        else:
            logger.error(f"Error describing data source: {e}")
            raise

def describe_dataset(quicksight_client, aws_account_id, dataset_id):
    try:
        response = quicksight_client.describe_data_set(
            AwsAccountId=aws_account_id,
            DataSetId=dataset_id
        )
        return response
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            return None
        else:
            logger.error(f"Error describing dataset: {e}")
            raise

def lambda_handler(event, context):
    # Retrieve environment variables
    aws_account_id = os.environ.get('AWS_ACCOUNT_ID', 'xxxxxxxxxxxx')
    manifest_bucket = os.environ.get('MANIFEST_BUCKET', 'imat-manifests')
    assessment_bucket = os.environ.get('ASSESSMENT_BUCKET', 'imat-assessments')
    table_map_bucket = os.environ.get('TABLE_MAP_BUCKET', 'imat-table-maps')
    template_nbr = os.environ.get('TEMPLATE_NUMBER', 'template')
    owner_arn = os.environ.get('OWNER_ARN', 'arn:aws:quicksight:us-east-1:xxxxxxxxxxxx:user/default/xxxxxxxxxxxx')

    logger.info("Pre-Processing Datasets Started")

    for record in event['Records']:
        # Parse the message from SQS
        message = json.loads(record['body'])
        bucket_name = message['bucket_name']
        object_key = message['object_key']

        if not object_key.lower().endswith('.json'):
            logger.warning(f"Non-json file found: {object_key}")
            continue

        logger.info(f"Processing manifest: {object_key}")
        parts = object_key.split('/')
        assessment_id = parts[0]

        manifest_file_name = parts[1]
        manifest_file_name_without_ext = os.path.splitext(manifest_file_name)[0]

        file_id = construct_file_id(object_key, assessment_id)
        file_name = file_id.replace('-', '_')

        datasource_name = f"{file_name}_datasource_{assessment_id}"
        datasource_id = f"{file_id}-datasource-{assessment_id}"

        dataset_name = f"{file_name}_dataset_{assessment_id}"
        dataset_id = f"{file_id}-dataset-{assessment_id}"
        template_dataset_id = f"{file_id}-dataset-{template_nbr}"
        
        cos_embark_key = f'assessments/{assessment_id}/cos-calculators/aggregated_embark.csv'
        logger.info(f"cos_embark_key: {cos_embark_key}")

        logger.info(f"assessment_id: {assessment_id}")
        logger.info(f"manifest_file_name: {manifest_file_name}")
        logger.info(f"manifest_file_name_without_ext: {manifest_file_name_without_ext}")
        logger.info(f"object_key: {object_key}")
        logger.info(f"file_id: {file_id}")
        logger.info(f"file_name: {file_name}")
        logger.info(f"datasource_name: {datasource_name}")
        logger.info(f"datasource_id: {datasource_id}")
        logger.info(f"dataset_name: {dataset_name}")
        logger.info(f"dataset_id: {dataset_id}")
        logger.info(f"Using Assessment#: {template_nbr} For Dashboard Template")
        logger.info(f"Template dataset_id: {template_dataset_id}")

        # Deduplication logic
        try:
            response = dynamodb.get_item(
                TableName=dynamodb_table,
                Key={
                    'assessment_id': {'S': assessment_id},
                    'dataset_id': {'S': dataset_id}
                }
            )
            if 'Item' in response and 'dataset_processed' in response['Item']:
                logger.info(f"Dataset {dataset_id} for assessment {assessment_id} already processed. Skipping.")
                continue
        except ClientError as e:
            logger.error(f"Error accessing DynamoDB: {e}")
            continue

        # Create Datasource
        try:
            physical_table_map, logical_table_map = get_table_maps(
                aws_account_id, s3_client, file_name, table_map_bucket, datasource_id
            )
            datasource_response = create_datasource(
                quicksight_client, aws_account_id, manifest_bucket, 
                object_key, datasource_id, datasource_name, owner_arn, max_retries=5
            )
            if not datasource_response:
                logger.error(f"Failed to create datasource {datasource_id}")
                logger.debug(f"Datasource creation failed response: {datasource_response}")
                return {
                    'statusCode': 500,
                    'body': f'Failed to create datasource {datasource_id}'
                }

            # Update DynamoDB for datasource processed
            dynamodb.update_item(
                TableName=dynamodb_table,
                Key={
                    'assessment_id': {'S': assessment_id},
                    'dataset_id': {'S': dataset_id}
                },
                UpdateExpression="SET datasource_processed = :val1, datasource_id = :val2",
                ExpressionAttributeValues={
                    ':val1': {'S': datetime.now().isoformat()},
                    ':val2': {'S': datasource_id}
                }
            )
        except Exception as e:
            logger.error(f"Error creating datasource: {e}")
            logger.debug(f"Datasource creation error details: {str(e)}")
            return {
                'statusCode': 500,
                'body': f'Error creating datasource: {e}'
            }

        # Create dataset
        try:
            dataset_response = create_dataset(
                quicksight_client, aws_account_id, datasource_id, dataset_id,
                dataset_name, physical_table_map, logical_table_map, owner_arn
            )
            if not dataset_response:
                logger.error(f"Failed to create dataset {dataset_id}")
                logger.debug(f"Dataset creation failed response: {dataset_response}")
                return {
                    'statusCode': 500,
                    'body': f'Failed to create dataset {dataset_id}'
                }

            # Update DynamoDB for dataset processed
            dynamodb.update_item(
                TableName=dynamodb_table,
                Key={
                    'assessment_id': {'S': assessment_id},
                    'dataset_id': {'S': dataset_id}
                },
                UpdateExpression="SET dataset_processed = :val1",
                ExpressionAttributeValues={
                    ':val1': {'S': datetime.now().isoformat()}
                }
            )
        except Exception as e:
            logger.error(f"Error creating dataset: {e}")
            logger.debug(f"Dataset creation error details: {str(e)}")
            return {
                'statusCode': 500,
                'body': f'Error creating dataset: {e}'
            }

        return {
            'statusCode': 200,
            'body': 'Dataset Processing Complete'
        }
