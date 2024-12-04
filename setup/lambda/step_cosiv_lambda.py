import pandas as pd
import boto3
from io import StringIO
import logging
import json


# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')


def read_s3_csv(s3_client, bucket_name, object_key):
    """Utility function to read CSV from S3."""
    obj = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    return pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')))


def check_file_exists(s3_client, bucket, key):
    """Check if a file exists in S3."""
    s3_client.head_object(Bucket=bucket, Key=key)


def cosiv_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket):
    """Process COS IV Embark data."""
    # Define file paths
    input_file_path = s3_key
    output_file_path = f'assessments/{assessment_id}/cosiv_embark.csv'

    # Check for the existence of the COS IV Embark CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("Class IV Embark file already exists.")
        return {
            'statusCode': 200,
            'body': 'File already exists',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Class IV Embark file does not exist, creating...")
        else:
            raise

    # Check for the input file to be available
    try:
        check_file_exists(s3_client, source_bucket, input_file_path)
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.error(f"Input file {input_file_path} does not exist.")
            return {
                'statusCode': 404,
                'body': f'Input file {input_file_path} does not exist.'
            }
        else:
            raise

    # Read data from S3
    cosiv_df = read_s3_csv(s3_client, source_bucket, object_key=input_file_path)

    # Rename columns
    cosiv_df = cosiv_df.rename(columns={
        'UOI_QTY': 'Qty',
        'Total_Price': 'Total_Cost',
        'Total_Weight_LB': 'Weight_lbs',
        'Total_Volume_CuFt': 'CUFT',
        'NIIN': 'COS_Type',
        'Price': 'Unit_Price',
        'Volume': 'volume',
        'Weight': 'weight'
    })

    # Add TEUS columns initialized to 0, and calculate STONS
    cosiv_df['TEUS'] = 0
    cosiv_df['STONS'] = cosiv_df['Weight_lbs'] / 2000

    # Filter rows where STONS > 0
    cosiv_df = cosiv_df[cosiv_df['STONS'] > 0]

    # Keep only the specified columns
    columns_to_keep = [
        'AssessmentNumber', 'Class_of_Supply', 'Region', 'Location_Name', 'Region_MEF_Lead',
        'COS_Type', 'Nomenclature', 'UI', 'Unit_Price', 'volume', 'weight', 'POS',
        'Qty', 'CUFT', 'TEUS', 'Weight_lbs', 'STONS', 'Total_Cost'
    ]

    cosiv_df = cosiv_df[columns_to_keep]

    # Convert all column names to uppercase
    cosiv_df.columns = cosiv_df.columns.str.upper()

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    cosiv_df.to_csv(csv_buffer, index=False)  # Removed `index=True` to avoid extra column in the output
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Class IV Embark CSV file has been uploaded to S3.")

    # Return the S3 file path
    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def lambda_handler(event, context):
    """Lambda function entry point."""
    logger.info("COS IV Embark transformation started.")
    # Log the received event
    logger.info(f"Received event: {json.dumps(event)}")

    target_bucket = '{bucket_name}'
    source_bucket = '{bucket_name}'
    s3_key = event.get('s3_key', '')
    assessment_id = event.get('assessment_id', '')

    if not s3_key or not assessment_id:
        logger.error("Missing required keys: 's3_key' or 'assessment_id'")
        return {
            'statusCode': 400,
            'body': "Missing required keys: 's3_key' or 'assessment_id'"
        }

    try:
        result = cosiv_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket)
        logger.info("COS IV Embark File Processed.")
        return result

    except Exception as e:
        logger.error(f"Error processing COS IV Embark: {str(e)}")
        return {
            'statusCode': 500,
            'body': f"Error processing COS IV Embark: {str(e)}"
        }
