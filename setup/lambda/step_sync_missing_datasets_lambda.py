import os
import json
import logging
from datetime import datetime
import boto3
import re
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
from tenacity import retry, stop_after_attempt, wait_fixed

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
dynamodb_client = boto3.client('dynamodb')
s3_client = boto3.client('s3')
quicksight_client = boto3.client('quicksight')
dynamodb_resource = boto3.resource('dynamodb', region_name='us-east-1')

# Configuration
dynamodb_table = os.getenv('DYNAMODB_TABLE', 'imat-dashboard-datasets')
aws_account_id = os.getenv('AWS_ACCOUNT_ID', 'xxxxxxxxxxxx')
region = os.getenv('AWS_REGION', 'us-east-1')


def parse_s3_file_path(s3_file_path):
    # Regex to extract components from the S3 path
    match = re.match(r's3://([^/]+)/assessments/([^/]+)/(.+)', s3_file_path)
    
    if match:
        source_bucket = match.group(1)  # Extract the bucket name
        assessment_id = match.group(2)   # Extract the assessment ID
        filename = match.group(3)         # Extract the filename
        
        return {
            'source_bucket': source_bucket,
            'assessment_id': assessment_id,
            'filename': filename
        }
    else:
        raise ValueError("Invalid S3 file path format.")


def check_dataset_exists(quicksight_client, aws_account_id, aggregated_dataset_id):
    try:
        quicksight_client.describe_data_set(
            AwsAccountId=aws_account_id,
            DataSetId=aggregated_dataset_id
        )
        return True
    except quicksight_client.exceptions.ResourceNotFoundException:
        return False
    except Exception as e:
        logger.error(f"Error checking dataset existence for {aggregated_dataset_id}: {e}")
        raise


def construct_file_metadata(object_key):
    file_name = object_key.split("/")[-1]
    file_name_without_ext = file_name.rsplit('.', 1)[0]
    file_id = file_name_without_ext.replace('_', '-')
    return file_id, file_name, file_name_without_ext
  

def check_s3_object_exists(s3_client, s3_path):
    try:
        bucket_name, key = s3_path.replace("s3://", "").split("/", 1)
        s3_client.head_object(Bucket=bucket_name, Key=key)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == "404":
            logger.info(f"File does not exist: {s3_path}")
            return False
        else:
            raise


def construct_dataset_id(object_key, assessment_id):
    file_name = object_key.split("/")[-1]
    file_name_without_ext = file_name.rsplit('.', 1)[0]
    file_id = file_name_without_ext.replace('_', '-')
    dataset_id = f'{file_id}-dataset-{assessment_id}'
    return dataset_id


def check_s3_object(bucket_name, object_key, assessment_id):
    """Check if the S3 object exists, return the dataset_id if object_key found."""
    try:
        s3_client.head_object(Bucket=bucket_name, Key=object_key)
        s3_dataset_id = construct_dataset_id(object_key, assessment_id)
        return s3_dataset_id
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.warning(f'Object {object_key} not found in bucket {bucket_name}')
            return None
        else:
            raise


def check_dynamodb_object(dynamodb_table, dataset_id, assessment_id):
    """Query DynamoDB for the dataset_id under the specified assessment_id."""
    try:
        table = dynamodb_resource.Table(dynamodb_table)
        response = table.query(
            KeyConditionExpression=Key('assessment_id').eq(f'{assessment_id}') & Key('dataset_id').eq(f'{dataset_id}'),
            FilterExpression=Attr('Dataset_Created').exists()
        )

        logger.info(f"DynamoDB response: {response}")

        if 'Items' in response and len(response['Items']) > 0:
            dynamodb_dataset_id = response['Items'][0].get('dataset_id')
            if dynamodb_dataset_id:
                logger.info(f"Extracted dataset_id from DynamoDB: {dynamodb_dataset_id}")
                return dynamodb_dataset_id
            else:
                logger.warning(f"No dataset_id found in DynamoDB for assessment_id: {assessment_id} and dataset_id: {dataset_id}")
                return None
        else:
            logger.warning(f"No items found in DynamoDB for assessment_id: {assessment_id} and dataset_id: {dataset_id}")
            return None

    except Exception as e:
        logger.error(f"An error occurred while querying DynamoDB: {e}")
        return None
      
      
@retry(stop=stop_after_attempt(10), wait=wait_fixed(5))
def check_all_embark_keys_against_dynamodb(assessment_id, s3_client, dynamodb_resource, dynamodb_table):
    """Check all embark S3 keys against DynamoDB dataset_ids."""
    lookup_table = {
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cos-calculators/AssessmentParameterDataMapTable.csv": f"AssessmentParameterDataMapTable-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cos-calculators/AssessmentTable.csv": f"AssessmentTable-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cosi_embark.csv": f"cosi-embark-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cosi_water_embark.csv": f"cosi-water-embark-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cosii_vii_embark.csv": f"cosii-vii-embark-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cosiiip_embark.csv": f"cosiiip-embark-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cosiv_embark.csv": f"cosiv-embark-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cosvi_embark.csv": f"cosvi-embark-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cosix_embark.csv": f"cosix-embark-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/ForceFlow_joined.csv": f"ForceFlow-joined-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/RD_I_POS_Location_joined.csv": f"RD-I-POS-Location-joined-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/RD_I_Ration_Costs_joined.csv": f"RD-I-Ration-Costs-joined-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/RD_IW_PaxLocationAll_joined.csv": f"RD-IW-PaxLocationAll-joined-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/RD_IW_Ration_Costs_joined.csv": f"RD-IW-Ration-Costs-joined-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cos-calculators/cos-i-water/output/RD_IW_POS_Requirement.csv": f"RD-IW-POS-Requirement-dataset-{assessment_id}",
        f"s3://wrsa-dev.canallc.com/assessments/{assessment_id}/cos-calculators/cos-ii-vii/output/RD_II_VII_DailyTE_WithDimensions.csv": f"RD-II-VII-DailyTE-WithDimensions-dataset-{assessment_id}"
    }

    matched_keys = []
    missing_dynamodb_matches = []
    synced_keys = []

    try:
        for s3_uri, dataset_id in lookup_table.items():
            s3_path = s3_uri.replace("s3://", "")
            bucket_name, object_key = s3_path.split('/', 1)

            try:
                # Check S3 Object
                s3_dataset_id = check_s3_object(bucket_name, object_key, assessment_id)
                if not s3_dataset_id:
                    # Check DynamoDB Object
                    dynamodb_dataset_id = check_dynamodb_object(dynamodb_table, dataset_id, assessment_id)
                    
                    if not dynamodb_dataset_id:
                        synced_keys.append((s3_uri, dataset_id))
                        logger.info(f"Dataset is synced as missing from both S3 and DynamoDB: {dataset_id}")
                    else:
                        missing_dynamodb_matches.append((s3_uri, dataset_id))
                        logger.warning(f"S3 object missing but found in DynamoDB: {dataset_id}")
                else:
                    # Check DynamoDB for a matching dataset
                    dynamodb_dataset_id = check_dynamodb_object(dynamodb_table, dataset_id, assessment_id)
                    if s3_dataset_id == dynamodb_dataset_id:
                        matched_keys.append((s3_dataset_id, dataset_id))
                        logger.info(f"Matched: S3 key {s3_dataset_id} with DynamoDB dataset_id: {dataset_id}")
                    else:
                        missing_dynamodb_matches.append((s3_uri, dataset_id))
                        logger.warning(f"UnMatched: S3 key {s3_uri} with DynamoDB dataset_id: {dataset_id}")

            except s3_client.exceptions.NoSuchKey:
                # Handle S3 object not found
                dynamodb_dataset_id = check_dynamodb_object(dynamodb_table, dataset_id, assessment_id)
                if not dynamodb_dataset_id:
                    synced_keys.append((s3_uri, dataset_id))
                    logger.info(f"Dataset is synced as missing from both S3 and DynamoDB: {dataset_id}")
                else:
                    missing_dynamodb_matches.append((s3_uri, dataset_id))
                    logger.warning(f"S3 object not found but exists in DynamoDB: {dataset_id}")

    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")

    finally:
        if matched_keys:
            logger.info(f"Matched S3 keys: {matched_keys}")
        if synced_keys:
            logger.info(f"Synced datasets (missing from both S3 and DynamoDB): {synced_keys}")
        if missing_dynamodb_matches:
            logger.warning(f"S3 objects with no DynamoDB matches: {missing_dynamodb_matches}")

    return {
        "matched_keys": matched_keys,
        "synced_keys": synced_keys,
        "unmatched_keys": missing_dynamodb_matches
    }

    
def update_sync_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id):
    timestamp = datetime.now().isoformat()
    try:
        dynamodb_client.update_item(
            TableName=dynamodb_table,
            Key={
                'assessment_id': {'S': assessment_id},
                'dataset_id': {'S': dataset_id}
            },
            UpdateExpression="SET #fr = :timestamp, #st = :status, #sts = :status_ts",
            ExpressionAttributeNames={
                '#fr': 'DATASETS_SYNCED!',
                '#st': 'Status',
                '#sts': 'Status_Timestamp'
            },
            ExpressionAttributeValues={
                ':timestamp': {'S': timestamp},
                ':status': {'S': 'DATASETS_SYNCED!'},
                ':status_ts': {'S': timestamp}
            }
        )
        logging.info(f"All datasets synced for final ingestion/data refresh for dataset_id: {dataset_id}")
    except Exception as e:
        logging.error(f"Error updating DynamoDB: {e}")



def lambda_handler(event, context):
    logger.info("Checking Dataset Sync Status")
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        if 'Records' in event:  # Triggered by DynamoDB Stream
            for record in event['Records']:
                if record['eventName'] in ['INSERT', 'MODIFY']:
                    new_image = record['dynamodb']['NewImage']
                    
                    # Extract and log the status if available
                    file_status = new_image.get('Status', {}).get('S', 'Unknown')
                    logger.info(f"File status: {file_status}")

                    if file_status == 'Dataset_Created':
                        assessment_id = new_image.get('assessment_id', {}).get('S')
                        dataset_id = new_image.get('dataset_id', {}).get('S')

                        # Check for dataset sync status
                        sync_result = check_all_embark_keys_against_dynamodb(
                            assessment_id, s3_client, dynamodb_resource, dynamodb_table
                        )

                        logger.info(f"Matched Keys: {sync_result['matched_keys']}")
                        logger.info(f"Unmatched Keys: {sync_result['unmatched_keys']}")

                        if len(sync_result['unmatched_keys']) == 0:
                            update_sync_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)
                            logger.info('Datasets Synced. S3 and DynamoDB Match!')
                        else:
                            logger.error(f"Datasets Not Synced. Awaiting: {sync_result['unmatched_keys']}")
                            return {
                                'statusCode': 500,
                                'matched_keys': sync_result.get('matched_keys', []),
                                'unmatched_keys': sync_result['unmatched_keys']
                            }

        else: 
            body = event.get("body")

            if isinstance(body, dict):
                assessment_id = body.get("assessment_id")
                matched_keys = body.get("matched_keys", [])
                unmatched_keys = body.get("unmatched_keys", [])
                num_missing = body.get("num_missing", 0)

                logger.info(f"Assessment ID: {assessment_id}")
                logger.info(f"Matched Keys: {matched_keys}")
                logger.info(f"Unmatched Keys: {unmatched_keys}")
                logger.info(f"Number of Missing Datasets: {num_missing}")

                if num_missing == 0:
                    dataset_id = f"aggregated-embark-dataset-{assessment_id}"
                    update_sync_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)
                    logger.info('Datasets Synced. S3 and DynamoDB Match!')
                    return {
                        'statusCode': 200,
                        'body': json.dumps({"message": "Processing complete"})
                    }

                elif num_missing < 3:
                    logger.info("Retrying due to one unmatched key...")
                    sync_result = check_all_embark_keys_against_dynamodb(
                        assessment_id, s3_client, dynamodb_resource, dynamodb_table
                    )
                    if len(sync_result['unmatched_keys']) == 0:
                        dataset_id = f"aggregated-embark-dataset-{assessment_id}"
                        update_sync_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)
                        logger.info('Datasets Synced. S3 and DynamoDB Match!')
                        return {
                            'statusCode': 200,
                            'body': json.dumps({"message": "Datasets synced after retry."})
                        }

                else:
                    logger.error(f"Datasets Not Synced. Awaiting: {unmatched_keys}")
                    return {
                        'statusCode': 500,
                        'matched_keys': matched_keys,
                        'unmatched_keys': unmatched_keys
                    }

            elif isinstance(body, str):
                s3_file_path = event.get("s3_file_path")
                logger.info(f"File already exists at: {s3_file_path}")

                try:
                    result = parse_s3_file_path(s3_file_path)
                    assessment_id = result['assessment_id']
                    source_bucket = result['source_bucket']
                    file_name = result['filename']

                    logger.info(f"Parsed File Info: Assessment ID: {assessment_id}, Bucket: {source_bucket}, Filename: {file_name}")

                    # Check for dataset sync status
                    sync_result = check_all_embark_keys_against_dynamodb(
                        assessment_id, s3_client, dynamodb_resource, dynamodb_table
                    )

                    logger.info(f"Matched Keys: {sync_result['matched_keys']}")
                    logger.info(f"Unmatched Keys: {sync_result['unmatched_keys']}")

                    if len(sync_result['unmatched_keys']) == 0:
                        dataset_id = f"aggregated-embark-dataset-{assessment_id}"
                        update_sync_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)
                        logger.info('Datasets Synced. S3 and DynamoDB Match!')
                    else:
                        logger.error(f"Datasets Not Synced. Awaiting: {sync_result['unmatched_keys']}")
                        return {
                            'statusCode': 500,
                            'matched_keys': sync_result.get('matched_keys', []),
                            'unmatched_keys': sync_result['unmatched_keys']
                        }

                    return {
                        'statusCode': 200,
                        'body': json.dumps({"message": "File already exists, no further action taken."})
                    }

                except ValueError as e:
                    logger.error(f"Error parsing S3 file path: {e}")
                    return {
                        'statusCode': 400,
                        'body': json.dumps({'error': str(e)})
                    }

            else:
                logger.error("Unexpected event format.")
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': 'Invalid event structure.'})
                }

    except Exception as e:
        logger.error(f"Error in lambda handler: {str(e)}", exc_info=True)
        return {"statusCode": 500, "body": f"Error occurred: {str(e)}"}
