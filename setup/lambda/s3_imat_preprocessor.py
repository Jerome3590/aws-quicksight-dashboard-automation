import boto3
import os
import json
import urllib.parse
import logging
import zipfile
import io
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

sts_client = boto3.client('sts')
sts_response = sts_client.assume_role(
    RoleArn='arn:aws:iam::548995328310:role/dynamoDB-cross-account',
    RoleSessionName='cross-account-access-session',
    DurationSeconds=900)

quicksight_dynamodb_client = boto3.client('dynamodb', region_name='us-east-1',
    aws_access_key_id=sts_response['Credentials']['AccessKeyId'],
    aws_secret_access_key=sts_response['Credentials']['SecretAccessKey'],
    aws_session_token=sts_response['Credentials']['SessionToken'])
    
quicksight_step_client = boto3.client('stepfunctions', region_name='us-east-1',
    aws_access_key_id=sts_response['Credentials']['AccessKeyId'],
    aws_secret_access_key=sts_response['Credentials']['SecretAccessKey'],
    aws_session_token=sts_response['Credentials']['SessionToken'])


# Environment variables
manifest_bucket = os.environ.get('MANIFEST_BUCKET', 'assessments-manifests')
dynamodb_table = os.environ.get('DYNAMODB_TABLE', 'imat-dashboard-datasets')
step_arn = os.environ.get('STEP_FUNCTION_ARN', 'arn:aws:states:us-east-1:548995328310:stateMachine:quicksight-data-processor')
region = os.environ.get('AWS_REGION', 'us-east-1')


# Helper functions
def construct_file_metadata(object_key):
    file_name = object_key.split("/")[-1]
    file_name_without_ext = file_name.rsplit('.', 1)[0]
    file_id = file_name_without_ext.replace('_', '-')
    return file_id, file_name, file_name_without_ext


def update_file_received_status(quicksight_dynamodb_client, dynamodb_table, assessment_id, dataset_id):
    timestamp = datetime.now().isoformat()
    try:
        quicksight_dynamodb_client.update_item(
            TableName=dynamodb_table,
            Key={
                'assessment_id': {'S': assessment_id},
                'dataset_id': {'S': dataset_id}
            },
            UpdateExpression="SET #fr = :timestamp, #st = :status, #sts = :status_ts",
            ExpressionAttributeNames={
                '#fr': 'File_Received',
                '#st': 'Status',
                '#sts': 'Status_Timestamp'
            },
            ExpressionAttributeValues={
                ':timestamp': {'S': timestamp},
                ':status': {'S': 'File_Received'},
                ':status_ts': {'S': timestamp}
            }
        )
        logging.info(f"File Received and timestamp updated for dataset_id: {dataset_id}")
    except Exception as e:
        logging.error(f"Error updating DynamoDB: {e}")


def extract_zip_file_and_upload(s3_client, source_bucket, object_key, assessment_id):
    zip_obj = s3_client.get_object(Bucket=source_bucket, Key=object_key)
    buffer = io.BytesIO(zip_obj["Body"].read())
    extracted_files = []
    with zipfile.ZipFile(buffer, 'r') as zip_ref:
        for file in zip_ref.namelist():
            file_data = zip_ref.read(file)
            extracted_key = f'assessments/{assessment_id}/extracted/{file}'
            s3_client.put_object(Bucket=source_bucket, Key=extracted_key, Body=file_data)
            if file.lower().endswith('.csv'):
                extracted_files.append(extracted_key)
    return extracted_files
  

def create_manifest(source_bucket, object_key, manifest_bucket, manifest_key):
    try:
        # Create the manifest
        manifest_json = {
            "entries": [{
                "url": f"s3://{source_bucket}/{object_key}",
                "mandatory": True
            }]
        }
        manifest_content = json.dumps(manifest_json, indent=4)
        s3_client.put_object(Bucket=manifest_bucket, Key=manifest_key, Body=manifest_content)
        logger.info(f"Manifest created: {manifest_key}")
    except s3_client.exceptions.ClientError as e:
        logger.error(f"Failed to create manifest: {e}")
        raise
      

# Predefined lists
EMBARK_JOIN_KEYS = {
    "RD_I_POS_Pallet_Requirement.csv",
    "RD_IW_PaxLocationAll_joined.csv",
    "RD_II_VII_DailyTE_WithDimensions.csv",
    "RD_IIIP_POL_Pkg_NSN_Requirements_PBI.csv",
    "RD_IV_Daily_Requirements.csv",
    "RD_VI_POS_Pallet_Requirement.csv",
    "RD_IX_Requirement_Trimmed.csv",
    "AssessmentTable.csv",
    "POS.csv",
    "cosi_embark.csv",
    "cosi_water_embark.csv",
    "cosii_vii_embark.csv",
    "cosiiip_embark.csv",
    "cosiv_embark.csv",
    "cosix_embark.csv",
    "cosvi_embark.csv"
}


def lambda_handler(event, context):
    logger.info("Pre-Processor started")
    logger.info(f"Received event: {json.dumps(event)}")

    for record in event['Records']:
        s3 = record['s3']
        source_bucket = s3['bucket']['name']
        object_key = urllib.parse.unquote_plus(s3['object']['key'])
        file_id, file_name, file_name_without_ext = construct_file_metadata(object_key)

        logger.info(f"Processing file: {file_id}")
        parts = object_key.split('/')

        if len(parts) > 1:
            assessment_id = parts[1]
            dataset_id = f'{file_id}-dataset-{assessment_id}'

            if object_key.lower().endswith('.zip'):
                extracted_files = extract_zip_file_and_upload(s3_client, source_bucket, object_key, assessment_id)
                logger.info(f"Zip File - Files extracted: {extracted_files}")
                logger.info("Zip File Processing complete. Exiting..")
                continue

            elif object_key.lower().endswith('.csv'):
                manifest_key = f"{assessment_id}/manifest_{file_name_without_ext}.json"
                create_manifest(source_bucket, object_key, manifest_bucket, manifest_key)
                update_file_received_status(quicksight_dynamodb_client, dynamodb_table, assessment_id, dataset_id)

                if file_name in EMBARK_JOIN_KEYS:
                    step_function_input = {
                        "assessment_id": assessment_id,
                        "file_id": file_id,
                        "file_name": file_name,
                        "s3_key": object_key,
                        "source_bucket": source_bucket
                    }

                    response = quicksight_step_client.start_execution(
                        stateMachineArn=step_arn,
                        input=json.dumps(step_function_input)
                    )
                    logger.info(f"Started Step Function Execution: {response['executionArn']}")
                else:
                    logger.info(f"File {file_name} not in EMBARK_JOIN_KEYS. Skipping Step Function execution.")

    logger.info("Initial Receipt Processing complete")
    return {'statusCode': 200, 'body': 'Processing complete'}
