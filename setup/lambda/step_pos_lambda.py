import pandas as pd
import boto3
from io import StringIO
import logging
import json


# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')
dynamodb_client = boto3.client('dynamodb')


def read_s3_csv(s3_client, bucket_name, object_key):
    """Utility function to read CSV from S3."""
    obj = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    return pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')))


def check_file_exists(s3_client, bucket, key):
    """Check if a file exists in S3 with retry logic."""
    logger.info(f"Checking existence of file: {bucket}/{key}")
    s3_client.head_object(Bucket=bucket, Key=key)


def get_POS(s3_client, source_bucket, assessment_id, target_bucket):
    # Define file paths
    input_file_path1 = f'assessments/{assessment_id}/cos-calculators/AssessmentTable.csv'
    input_file_path2 = f'assessments/{assessment_id}/cos-calculators/AssessmentParameterDataMapTable.csv'
    output_file_path = f'assessments/{assessment_id}/POS.csv'

    # Check for the existence of the POS.CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("POS.csv file already exists")
        return
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("POS.csv file does not exist, creating...")
        else:
            raise

    # Check for files to be available with retry logic
    try:
        check_file_exists(s3_client, source_bucket, input_file_path1)
        check_file_exists(s3_client, source_bucket, input_file_path2)
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.error("One or both of the input files do not exist.")
            return {
                'statusCode': 404,
                'body': 'One or both of the input files do not exist.'
            }
        else:
            raise

    df1 = read_s3_csv(s3_client, source_bucket, object_key=input_file_path1)
    df2 = read_s3_csv(s3_client, source_bucket, object_key=input_file_path2)

    # Merge the dataframes on ID
    merged_df = pd.merge(df1, df2, right_on='AssessmentID', left_on='AssessmentNumber')

    # Create the new dataframe with the specified columns and format
    new_data = {
        'POS': ['POS1', 'POS2', 'POS3', 'BEYOND'],
        'DOS': ['DOS1', 'DOS2', 'DOS3', 'BEYOND'],
        'Period': [
            merged_df['DaysOfSupplyFirst'].iloc[0],
            merged_df['DaysOfSupplySecond'].iloc[0],
            merged_df['DaysOfSupplyThird'].iloc[0],
            merged_df['OperationDuration'].iloc[0]
        ],
        'Sort_Index': [1, 2, 3, 4]
    }

    pos_df = pd.DataFrame(new_data)

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    pos_df.to_csv(csv_buffer, index=False)  # Use index=False to match the expected format
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("POS.csv file has been uploaded to S3.")

    # Return the S3 file path
    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }

def lambda_handler(event, context):
    logger.info("POS Join transformation started")
    # Log the received event
    logger.info(f"Received event: {json.dumps(event)}")

    target_bucket = 'wrsa-dev.canallc.com'
    source_bucket = 'wrsa-dev.canallc.com'
    s3_key = event['s3_key']
    file_id = event['file_id']
    file_name = event['file_name']
    assessment_id = event['assessment_id']

    result = get_POS(s3_client, source_bucket, assessment_id, target_bucket)

    logger.info("POS Join File Processed")
    return result
