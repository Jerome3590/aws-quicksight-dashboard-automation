import os
import json
import logging
import pandas as pd
import boto3
from io import StringIO
from datetime import datetime
from botocore.exceptions import ClientError


# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
s3_client = boto3.client('s3')
dynamodb_client = boto3.client('dynamodb')

# Configuration
dynamodb_table = os.getenv('DYNAMODB_TABLE', 'imat-dashboard-datasets')
aws_account_id = os.getenv('AWS_ACCOUNT_ID', 'xxxxxxxxxxxx')
region = os.getenv('AWS_REGION', 'us-east-1')


def parse_s3_path(s3_path):
    parsed_url = s3_path.replace("s3://", "").split("/", 1)
    bucket_name = parsed_url[0]
    key = parsed_url[1]
    return bucket_name, key


def list_s3_files(s3_client, bucket_name, prefix):
    """Utility function to list all files in an S3 bucket with a specific prefix."""
    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        if 'Contents' in response:
            return [item['Key'] for item in response['Contents']]
        else:
            return []
    except Exception as e:
        logger.error(f"Error listing files in bucket {bucket_name} with prefix {prefix}: {e}")
        return []


def read_s3_csv(s3_client, bucket_name, key):
    """Utility function to read CSV from S3."""
    response = s3_client.get_object(Bucket=bucket_name, Key=key)
    return pd.read_csv(StringIO(response['Body'].read().decode('utf-8')))


def read_and_process_file(s3_client, s3_path):
    bucket_name, key = parse_s3_path(s3_path)
    df = read_s3_csv(s3_client, bucket_name, key)
    df.columns = df.columns.str.strip().str.upper()
    required_columns = ['CLASS_OF_SUPPLY', 'REGION', 'LOCATION_NAME',
                        'POS', 'CUFT', 'SQFT', 'TEUS', 'WEIGHT_LBS', 'TOTAL_COST'] 
    df = df.reindex(columns=required_columns, fill_value=0)
    return df
  

def agg_embark_files(bucket_name, embark_paths, output_file_path):
  
    aggregated_df = pd.DataFrame()
    for s3_path in embark_paths:
        try:
            logger.info(f"Processing file: {s3_path}")
            df = read_and_process_file(s3_client, s3_path)
            logger.info(f"Processed {len(df)} rows from {s3_path}")
            aggregated_df = pd.concat([aggregated_df, df], ignore_index=True)
        except Exception as e:
            logger.error(f"Error processing file {s3_path}: {e}")

    aggregated_df.fillna(0, inplace=True)
    
    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    aggregated_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=bucket_name, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info(f"Aggregated Embark file has been uploaded to S3 at {output_file_path}.")


def update_embark_agg_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id):
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
                '#fr': 'Embark_Files_Aggregrated',
                '#st': 'Status',
                '#sts': 'Status_Timestamp'
            },
            ExpressionAttributeValues={
                ':timestamp': {'S': timestamp},
                ':status': {'S': 'Embark_Files_Aggregrated'},
                ':status_ts': {'S': timestamp}
            }
        )
        logging.info(f"Embark files aggrated for dataset_id: {dataset_id}")
    except Exception as e:
        logging.error(f"Error updating DynamoDB: {e}")
        

def lambda_handler(event, context):
    logger.info("Embark Aggregation Started..")
    logger.info(f"Received event: {json.dumps(event)}")

    assessment_id = event['assessment_id']
    source_bucket = event['source_bucket']
    
    bucket_name = source_bucket
    
    dataset_id = f'aggregated-embark-dataset-{assessment_id}'
    dataset_name = f'aggregated_embark_dataset_{assessment_id}'
    file_id = f'aggregated-embark'
    file_name = f'aggregated_embark.csv'
    output_file_path = f"assessments/{assessment_id}/aggregated_embark.csv"


    try:
        
        # List all cos*embark.csv files in the source bucket
        prefix = f"assessments/{assessment_id}/"
        
        embark_paths = []
  
        for key in list_s3_files(s3_client, bucket_name, prefix):
            if key.startswith(f'{prefix}cos') and key.endswith('embark.csv'):
                embark_path = f"s3://{bucket_name}/{key}"
                embark_paths.append(embark_path)
                logger.info(f"Selected for processing: {embark_path}")
        
        if not embark_paths:
            raise Exception("No embark files found to process.")
        
        logger.info(f"Embark S3 Paths: {json.dumps(embark_paths)}")
        
        object_key = f"assessments/{assessment_id}/aggregated_embark.csv"
        
        agg_embark_files(bucket_name, embark_paths, output_file_path)
        
        # Update aggregation embark completion status in DynamoDB
        update_embark_agg_status(dynamodb_client, dynamodb_table, assessment_id, dataset_id)
        
        # Return the S3 file path
        return {
            'statusCode': 200,
            'body': 'Aggregated Embark File Processing complete',
            's3_file_path': f's3://{bucket_name}/{object_key}'
        }

    except Exception as e:
        logger.error(f"Error in lambda handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'message': 'Error processing files'})
        }
