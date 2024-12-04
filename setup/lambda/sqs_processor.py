import boto3
import os
import logging
import json
import shutil
from datetime import datetime
import traceback
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, stop_after_delay, wait_fixed


# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture all log levels


# Initialize AWS clients outside the Lambda handler for reuse
s3_client = boto3.client('s3')
quicksight_client = boto3.client('quicksight')
dynamodb_client = boto3.client('dynamodb')


# Local files
source_path = '/var/task/table_maps/'
destination_path = '/tmp/table_maps/'

# Ensure the destination directory exists
os.makedirs(destination_path, exist_ok=True)


# Copy files to /tmp directory
for filename in os.listdir(source_path):
    shutil.copy(os.path.join(source_path, filename), os.path.join(destination_path, filename))


# Paths to the lookup table and templates
lookup_table_path = os.path.join(destination_path, 'template_lookup.json')
templates_directory = destination_path


def copy_files_to_tmp(source_path, destination_path):
    # Ensure the destination directory exists
    os.makedirs(destination_path, exist_ok=True)

    # Copy files to /tmp directory
    for filename in os.listdir(source_path):
        shutil.copy(os.path.join(source_path, filename), os.path.join(destination_path, filename))


def construct_file_name_from_manifest(object_key):
    # Split the object key by "/" to get the full prefix parts
    full_s3_prefix = object_key.split("/")
    # Extract the filename (the last part of the split)
    file_name = full_s3_prefix[-1]
    # Remove the extension from the filename
    file_name_without_ext = file_name.rsplit('.', 1)[0]
    # remove the prefix from the filename
    file_name = file_name_without_ext.replace('manifest_', '')
    return file_name


def construct_file_id_from_manifest(object_key):
    # Split the object key by "/" to get the full prefix parts
    full_s3_prefix = object_key.split("/")
    # Extract the filename (the last part of the split)
    file_name = full_s3_prefix[-1]
    # Remove the extension from the filename
    file_name_without_ext = file_name.rsplit('.', 1)[0]
    # remove the prefix from the filename
    file_name = file_name_without_ext.replace('manifest_', '')
    file_id = file_name.replace('_', '-')
    return file_id


def get_object(s3_client, bucket_name, key_name):
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=key_name)
        return response['Body'].read().decode('utf-8')
    except ClientError as e:
        logging.error(f"Error getting object {key_name} from bucket {bucket_name}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error getting object {key_name} from bucket {bucket_name}: {e}")
        return None


def read_txt_file(file_path):
    try:
        with open(file_path, 'r') as file:
            data = file.read()
            logger.info(f"Successfully read text from {file_path}")
            return data
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {str(e)}")
    return None



def get_table_maps(aws_account_id, file_name, datasource_id, assessment_id):
    destination_path = '/tmp/table_maps/'

    logical_map_path = os.path.join(destination_path, f'{file_name}_logical_map.json')
    physical_map_path = os.path.join(destination_path, f'{file_name}_physical_map.json')

    logging.info(f"Processing Table Map for: {file_name}")

    if not os.path.exists(logical_map_path) or not os.path.exists(physical_map_path):
        raise ValueError("Failed to retrieve one or both of the map files from local directory")

    logical_table_map = read_txt_file(logical_map_path)
    if logical_table_map is None:
        raise ValueError(f"Failed to read logical table map from {logical_map_path}")
    updated_logical_table_map = logical_table_map.replace('template', f'{assessment_id}')
    logical_table_map = json.loads(updated_logical_table_map)
	
    physical_table_map = read_txt_file(physical_map_path)
    if physical_table_map is None:
        raise ValueError(f"Failed to read physical table map from {physical_map_path}")
    updated_physical_table_map = physical_table_map.replace('template', f'{assessment_id}')
    physical_table_map = json.loads(updated_physical_table_map)

    return physical_table_map, logical_table_map


def exponential_backoff(retries):
    import time
    time.sleep(2 ** retries)


def create_datasource(quicksight_client, aws_account_id, manifest_bucket, object_key, datasource_id, datasource_name, owner_arn, max_retries=5):
    retries = 0
    while retries < max_retries:
        try:
            data_source_response = quicksight_client.create_data_source(
                AwsAccountId=aws_account_id,
                DataSourceId=datasource_id,
                Name=datasource_name,
                Type='S3',
                DataSourceParameters={
                    'S3Parameters': {
                        'ManifestFileLocation': {
                            'Bucket': manifest_bucket,
                            'Key': object_key
                        }
                    }
                },
                Permissions=[
                    {
                        'Principal': owner_arn,
                        'Actions': [
                            'quicksight:DescribeDataSource',
                            'quicksight:DescribeDataSourcePermissions',
                            'quicksight:PassDataSource',
                            'quicksight:UpdateDataSource',
                            'quicksight:DeleteDataSource',
                            'quicksight:UpdateDataSourcePermissions'
                        ]
                    }
                ],
                SslProperties={
                    'DisableSsl': False
                }
            )
            logger.info(f'Datasource created successfully: {data_source_response}')
            return data_source_response
        except quicksight_client.exceptions.ResourceExistsException:
            logger.info(f"Datasource {datasource_id} already exists. Skipping creation.")
            return None
        except Exception as e:
            error_message = (
                f"Error creating datasource (Bucket: {manifest_bucket}, Key: {object_key}, "
                f"Datasource ID: {datasource_id}, Attempt: {retries + 1}): {str(e)}"
            )
            logger.error(error_message)
            logger.error(traceback.format_exc())  # Logs the full traceback for better debugging
            if retries == max_retries - 1:
                logger.error(f"Max retries reached. Failed to create datasource {datasource_id}")
                return None
            retries += 1
            exponential_backoff(retries)


def create_dataset(quicksight_client, aws_account_id, datasource_id, dataset_id, dataset_name, physical_table_map, logical_table_map, owner_arn, max_retries=5):
    retries = 0
    while retries < max_retries:
        try:
            response = quicksight_client.create_data_set(
                AwsAccountId=aws_account_id,
                DataSetId=dataset_id,
                Name=dataset_name,
                PhysicalTableMap=physical_table_map,
                LogicalTableMap=logical_table_map,
                ImportMode='SPICE',
                Permissions=[
                    {
                        'Principal': owner_arn,
                        'Actions': [
                            'quicksight:UpdateDataSetPermissions',
                            'quicksight:DescribeDataSet',
                            'quicksight:DescribeDataSetPermissions',
                            'quicksight:PassDataSet',
                            'quicksight:DescribeIngestion',
                            'quicksight:ListIngestions',
                            'quicksight:UpdateDataSet',
                            'quicksight:DeleteDataSet',
                            'quicksight:CreateIngestion',
                            'quicksight:CancelIngestion'
                        ]
                    }
                ]
            )
            logger.info(f'Dataset created successfully: {response}')
            return response
        except quicksight_client.exceptions.ResourceExistsException:
            logger.info(f"Dataset {dataset_id} already exists. Skipping creation.")
            return None
        except Exception as e:
            error_message = (
                f"Error creating dataset (Datasource ID: {datasource_id}, Dataset ID: {dataset_id}, "
                f"Attempt: {retries + 1}): {str(e)}"
            )
            logger.error(error_message)
            logger.error(traceback.format_exc())  # Logs the full traceback for better debugging
            if retries == max_retries - 1:
                logger.error(f"Max retries reached. Failed to create dataset {dataset_id}")
                return None
            retries += 1
            exponential_backoff(retries)


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


def check_s3_object_exists(s3_path):
    
    try:
        # Parse bucket and key from the s3_path
        parsed_url = s3_path.replace("s3://", "").split("/", 1)
        bucket_name = parsed_url[0]
        key = parsed_url[1]

        # Check if the object exists
        s3.head_object(Bucket=bucket_name, Key=key)
        return True
    except s3.exceptions.NoSuchKey:
        return False
    except Exception as e:
        logger.error(f"Error checking S3 object {s3_path}: {e}")
        return False


def update_datasource_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id):
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
                '#fr': 'Datasource_Created',
                '#st': 'Status',
                '#sts': 'Status_Timestamp'
            },
            ExpressionAttributeValues={
                ':timestamp': {'S': timestamp},
                ':status': {'S': 'Datasource_Created'},
                ':status_ts': {'S': timestamp}
            }
        )
        logging.info(f"Datasource created and timestamp updated for dataset_id: {dataset_id}")
    except Exception as e:
        logging.error(f"Error updating DynamoDB for dataset_id {dataset_id}: {e}")
        logging.debug("Error details: ", exc_info=True)  # Include traceback for debugging

        
        
def update_dataset_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id):
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
                '#fr': 'Dataset_Created',
                '#st': 'Status',
                '#sts': 'Status_Timestamp'
            },
            ExpressionAttributeValues={
                ':timestamp': {'S': timestamp},
                ':status': {'S': 'Dataset_Created'},
                ':status_ts': {'S': timestamp}
            }
        )
        logging.info(f"Dataset created and timestamp updated for dataset_id: {dataset_id}")
    except Exception as e:
        logging.error(f"Error updating DynamoDB: {e}")


# Copy files prior to calling the function
source_path = '/var/task/table_maps/'
destination_path = '/tmp/table_maps/'
copy_files_to_tmp(source_path, destination_path)


# Define the retry configuration
@retry(stop=stop_after_delay(120), wait=wait_fixed(10))
def create_datasource_with_retry(*args, **kwargs):
    return create_datasource(*args, **kwargs)

@retry(stop=stop_after_delay(120), wait=wait_fixed(10))
def create_dataset_with_retry(*args, **kwargs):
    return create_dataset(*args, **kwargs)


@retry(stop=stop_after_attempt(10), wait=wait_fixed(3))
def lambda_handler(event, context):
    # Retrieve environment variables
    aws_account_id = os.environ.get('AWS_ACCOUNT_ID', '548995328310')
    manifest_bucket = os.environ.get('MANIFEST_BUCKET', 'assessments-manifests')
    assessment_bucket = os.environ.get('ASSESSMENT_BUCKET', 'wrsa-dev.canallc.com')
    owner_arn = os.environ.get('OWNER_ARN', 'arn:aws:quicksight:us-east-1:548995328310:user/default/quicksight')
    dynamodb_table = os.environ.get('DYNAMODB_TABLE', 'imat-dashboard-datasets')
    
    not_needed_but_used = [
        'RD_IIIP_POL_Pkg_NSN_Requirements_PBI',
        'RD_IV_Daily_Requirements', 
        'RD_VI_POS_Pallet_Requirement',
        'RD-II-ICCE-DailyTE'
    ]

    logger.info("Processing Dataset..")

    for record in event['Records']:
        try:
            # Parse the message from SQS
            message = json.loads(record['body'])
            logger.debug(f"Received message: {message}")

            # Extract the bucket name and object key
            s3_event = message['Records'][0]
            bucket_name = s3_event['s3']['bucket']['name']
            object_key = s3_event['s3']['object']['key']

            if not object_key.lower().endswith('.json'):
                logger.warning(f"Non-json file found: {object_key}")
                continue
              
            logger.info(f"Processing manifest: {object_key}")
            parts = object_key.split('/')
            assessment_id = parts[0]
            manifest_file_name = parts[1]

            file_id = construct_file_id_from_manifest(object_key)
            file_name = construct_file_name_from_manifest(object_key)
            
            if any(file_name.startswith(dataset) for dataset in not_needed_but_used):
                logger.warning(f"File {file_name} not needed for final dashboard. Skipping creation.")
                continue

            datasource_id = f"{file_id}-datasource-{assessment_id}"
            dataset_id = f"{file_id}-dataset-{assessment_id}"

            logger.info(f"Assessment ID: {assessment_id}, File ID: {file_id}, Datasource ID: {datasource_id}, Dataset ID: {dataset_id}")

            # Check if the datasource already exists
            if not describe_data_source(quicksight_client, aws_account_id, datasource_id):
                try:
                    datasource_response = create_datasource_with_retry(
                        quicksight_client, aws_account_id, manifest_bucket, 
                        object_key, datasource_id, f"{file_name}_datasource_{assessment_id}", owner_arn
                    )
                    if not datasource_response:
                        logger.error(f"Failed to create datasource {datasource_id}")
                        continue
                except Exception as e:
                    logger.error(f"Error creating datasource: {e}")
                    continue

            # Check if the dataset already exists
            if not describe_dataset(quicksight_client, aws_account_id, dataset_id):
                try:
                    physical_table_map, logical_table_map = get_table_maps(
                        aws_account_id, file_name, datasource_id, assessment_id
                    )
                    dataset_response = create_dataset_with_retry(
                        quicksight_client, aws_account_id, datasource_id, dataset_id,
                        f"{file_name}_dataset_{assessment_id}", physical_table_map, logical_table_map, owner_arn
                    )
                    if not dataset_response:
                        logger.error(f"Failed to create dataset {dataset_id}")
                        continue
                except Exception as e:
                    logger.error(f"Error creating dataset: {e}")
                    continue

            # Update DynamoDB for processed datasource and dataset
            update_datasource_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)
            update_dataset_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)

        except Exception as e:
            logger.error(f"Failed to process record: {str(e)}")
            continue

    return {
        'statusCode': 200,
        'body': 'Dataset processing complete'
    }
