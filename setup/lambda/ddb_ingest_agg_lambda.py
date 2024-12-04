import os
import json
import logging
import time
import csv
from io import StringIO
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError


# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
s3_client = boto3.client('s3')
dynamodb_client = boto3.client('dynamodb')
dynamodb_resource = boto3.resource('dynamodb', region_name='us-east-1')
quicksight_client = boto3.client('quicksight')

# Configuration
aws_account_id = os.getenv('AWS_ACCOUNT_ID', 'xxxxxxxxxxxx')
region = os.getenv('AWS_REGION', 'us-east-1')
dynamodb_table = os.getenv('DYNAMODB_TABLE', 'imat-dashboard-datasets')
source_bucket = os.environ.get('SOURCE_BUCKET', '{bucket_name}')


def count_csv_rows(bucket_name, file_key):
    """
    Count the number of rows in a CSV file stored in S3.
    """
    try:
        # Initialize S3 client
        s3_client = boto3.client('s3')
        
        # Fetch the file from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        
        # Read the content of the CSV file
        csv_content = response['Body'].read().decode('utf-8')
        
        # Use StringIO to create a file-like object for csv.reader
        csv_data = StringIO(csv_content)
        
        # Use csv.reader to count the rows
        reader = csv.reader(csv_data)
        row_count = sum(1 for row in reader) - 1  # Subtract 1 to exclude the header

        return row_count
    except Exception as e:
        print(f"Error counting rows in CSV: {str(e)}")
        return None


def get_dataset_row_count(quicksight_client, aws_account_id, dataset_id):
    """
    Get the number of rows ingested in a QuickSight dataset.
    """
    try:
        # List all ingestions for the dataset
        response = quicksight_client.list_ingestions(
            AwsAccountId=aws_account_id,
            DataSetId=dataset_id
        )
        
        # Get the most recent ingestion ID
        latest_ingestion = response['Ingestions'][0]
        ingestion_id = latest_ingestion['IngestionId']
        
        # Describe the ingestion to get details
        ingestion_details = quicksight_client.describe_ingestion(
            AwsAccountId=aws_account_id,
            DataSetId=dataset_id,
            IngestionId=ingestion_id
        )
        
        # Get the number of rows ingested
        row_info = ingestion_details['Ingestion']['RowInfo']
        rows_ingested = row_info['RowsIngested']
        
        return rows_ingested
    
    except Exception as e:
        print(f"Error retrieving row count from QuickSight: {str(e)}")
        return None


def compare_s3_and_quicksight_rows(quicksight_client, bucket_name, file_key, assessment_id, aws_account_id):
    """
    Compare the number of rows in S3 CSV (aggregated_embark.csv) and the corresponding QuickSight dataset.
    If the number of rows is the same, return True, otherwise return False.
    """
    try:
        
        # Count rows in S3 file (aggregated_embark.csv)
        s3_row_count = count_csv_rows(bucket_name, file_key)
        if s3_row_count is None:
            print("Failed to count rows in S3 CSV file.")
            return False
        
        # Build the dataset ID for QuickSight
        dataset_id = f'aggregated-embark-dataset-{assessment_id}'
        
        # Get the number of rows in QuickSight dataset
        quicksight_row_count = get_dataset_row_count(quicksight_client, aws_account_id, dataset_id)
        if quicksight_row_count is None:
            print("Failed to retrieve row count from QuickSight dataset.")
            return False
        
        # Compare the two row counts
        if s3_row_count == quicksight_row_count:
            print("Row counts match.")
            return True
        else:
            print(f"Row counts do not match. S3: {s3_row_count}, QuickSight: {quicksight_row_count}")
            return False
        
    except Exception as e:
        print(f"Error comparing rows: {str(e)}")
        return False


def parse_dataset_id(dataset_id, assessment_id):
    # Remove the '-dataset-{assessment_id}' part
    parsed_id = dataset_id.replace(f'-dataset-{assessment_id}', '')
    return parsed_id


def update_ingest_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id):
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
                '#fr': 'INGEST_COMPLETE',
                '#st': 'Status',
                '#sts': 'Status_Timestamp'
            },
            ExpressionAttributeValues={
                ':timestamp': {'S': timestamp},
                ':status': {'S': 'INGEST_COMPLETE'},
                ':status_ts': {'S': timestamp}
            }
        )
        logging.info(f"Ingestion complete for dataset_id: {dataset_id}")
    except Exception as e:
        logging.error(f"Error updating DynamoDB: {e}")


def create_ingestion(quicksight_client, aws_account_id, aggregated_dataset_id, assessment_id, source_bucket):
    try:
        # Ensure the dataset exists before creating an ingestion
        if not check_dataset_exists(quicksight_client, aws_account_id, aggregated_dataset_id):
            logger.error(f"Dataset {aggregated_dataset_id} does not exist.")
            return None, None
        
        # Define the S3 bucket and file path
        bucket_name = source_bucket
        file_key = f'assessments/{assessment_id}/aggregated_embark.csv'
        
        # Compare the number of rows between S3 and QuickSight dataset
        test = compare_s3_and_quicksight_rows(quicksight_client, bucket_name, file_key, assessment_id, aws_account_id)
        
        if test:
            logger.info("Row count match between S3 and QuickSight dataset. No ingestion created.")
            return None, None  # Return early when rows match, no ingestion needed
        
        # Create a new ingestion with FULL_REFRESH to reset the dataset
        ingestion_id = f'ingestion-{datetime.now().strftime("%Y%m%d%H%M%S")}'
        response = quicksight_client.create_ingestion(
            DataSetId=aggregated_dataset_id,
            IngestionId=ingestion_id,
            AwsAccountId=aws_account_id,
            IngestionType='FULL_REFRESH'
        )
        
        logger.info(f"Ingestion {ingestion_id} created successfully for dataset {aggregated_dataset_id}.")
        return response, ingestion_id

    except Exception as e:
        logger.error(f"Error creating ingestion for dataset {aggregated_dataset_id}: {e}")
        raise


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


def describe_ingestion_status(quicksight_client, aws_account_id, aggregated_dataset_id, ingestion_id):
    try:
        response = quicksight_client.describe_ingestion(
            AwsAccountId=aws_account_id,
            DataSetId=aggregated_dataset_id,
            IngestionId=ingestion_id
        )
        status = response['Ingestion']['IngestionStatus']
        return status
    except Exception as e:
        logger.error(f"Error describing ingestion status for {ingestion_id} in dataset {aggregated_dataset_id}: {e}")
        raise


def wait_for_ingestion_completion(quicksight_client, aws_account_id, aggregated_dataset_id, ingestion_id, poll_interval=30, max_attempts=10):
    attempts = 0
    while attempts < max_attempts:
        status = describe_ingestion_status(quicksight_client, aws_account_id, aggregated_dataset_id, ingestion_id)
        logger.info(f"Ingestion status for {ingestion_id}: {status}")
        if status in ['COMPLETED', 'FAILED', 'CANCELLED']:
            return status
        time.sleep(poll_interval)
        attempts += 1
    return 'TIMEOUT'
  
  
def lambda_handler(event, context):
    
    logger.info(f"Received event: {json.dumps(event)}")
    
    try:
        # Process each record in the DynamoDB stream
        for record in event['Records']:
            if record['eventName'] in ['INSERT', 'MODIFY']:
                new_image = record['dynamodb']['NewImage']
                
                # Extract and log the status if available
                file_status = new_image.get('Status', {}).get('S', 'Unknown')
                logger.info(f"File status: {file_status}")
    
                if file_status == 'DATASETS_SYNCED!':
                    assessment_id = new_image.get('assessment_id', {}).get('S')
                    dataset_id = new_image.get('dataset_id', {}).get('S')
                    
                    parsed_id = parse_dataset_id(dataset_id, assessment_id)
                    logger.info(f"Parsed Dataset_Id: {parsed_id}")
                    
                    # Exit if parsed_id does not start with 'cos' or 'aggregated'
                    if not (parsed_id.startswith('cos') or parsed_id.startswith('aggregated')):
                        logger.info(f"Parsed ID {parsed_id} does not match prefix 'cos' or 'aggregated'. Exiting function.")
                        return {"statusCode": 200, "body": "Exiting: Dataset does not match expected prefixes."}
                    
                    # Proceed with further processing if parsed_id meets conditions
                    aggregated_dataset_id = f"aggregated-embark-dataset-{assessment_id}"
                    
                    # Check if the dataset exists in QuickSight
                    if check_dataset_exists(quicksight_client, aws_account_id, aggregated_dataset_id):
                      
                        logger.info("Starting Aggregated Embark Ingest..")
                        # Trigger QuickSight ingestion
                        ingestion_response, ingestion_id = create_ingestion(quicksight_client, aws_account_id, aggregated_dataset_id, assessment_id, source_bucket)
                        
                        # Check if ingestion_id is None before proceeding
                        if ingestion_id is None:
                            logger.info("No ingestion created due to row count match or an error.")
                            continue  # Skip further processing if no ingestion
                        else:
                            # Proceed with describing the ingestion or further processing
                            logger.info(f"Processing ingestion with ID: {ingestion_id}")
                            
                            # Wait for ingestion completion and log final status
                            final_status = wait_for_ingestion_completion(quicksight_client, aws_account_id, aggregated_dataset_id, ingestion_id)
                            logger.info(f"Ingestion {ingestion_id} for dataset {aggregated_dataset_id} completed with status: {final_status}")
                            
                            if final_status == 'COMPLETED':
                                # Update ingestion completion status in DynamoDB
                                update_ingest_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)
                                
                                logger.info('Ingestion completed successfully.')
                            else:
                                logger.error(f"Ingestion failed with status: {final_status}")
                                return {"statusCode": 500, "body": f"Ingestion failed with status: {final_status}"}
                    else:
                        logger.error(f"Dataset {aggregated_dataset_id} does not exist in QuickSight.")
                        return {"statusCode": 404, "body": f"Dataset {aggregated_dataset_id} does not exist in QuickSight."}

        return {"statusCode": 200, "body": "Lambda processed successfully"}

    except Exception as e:
        logger.error(f"Error in lambda handler: {str(e)}", exc_info=True)
        return {"statusCode": 500, "body": f"Error occurred: {str(e)}"}

