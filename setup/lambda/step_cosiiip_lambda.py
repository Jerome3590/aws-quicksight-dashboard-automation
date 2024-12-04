import pandas as pd
import boto3
import numpy as np
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


def cosiiip_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket):
    """Process COS IIIP Embark data."""
    # Define file paths
    input_file_path = s3_key
    output_file_path = f'assessments/{assessment_id}/cosiiip_embark.csv'

    # Check for the existence of the COS IIIP Embark CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("Class IIIP Embark file already exists.")
        return {
            'statusCode': 200,
            'body': 'File already exists',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Class IIIP Embark file does not exist, creating...")
        else:
            raise

    # Check for the input file to be available with retry logic
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
    cosiiip_df = read_s3_csv(s3_client, source_bucket, object_key=input_file_path)

    # Rename columns
    cosiiip_df = cosiiip_df.rename(columns={
        'IND STON': 'ston_',
        'IND CUFT': 'cuft_',
        'IND TEUs_Cube': 'teu_',
        'IND SQFT_Cube': 'sqft_',
        'MEF': 'Region_MEF_Lead',
        'POL_Type': 'COS_Type',
        'POL_Pkg_Type': 'UI',
        'REGION': 'Region',
        'LOCATION_NAME': 'Location_Name',
        'STANDARD UNIT PRICE': 'Unit_Price'
    })

    # Drop the specified columns
    columns_to_drop = [
        'TAMCN', 'NSN', 'CEC', 'SOS', 'IND SQFT_LW'
    ]
    cosiiip_df = cosiiip_df.drop(columns=columns_to_drop, errors='ignore')

    # Calculations
    cosiiip_df['STONS'] = cosiiip_df['Qty'] * cosiiip_df['ston_']
    cosiiip_df['Weight_lbs'] = cosiiip_df['STONS'] / 2000
    cosiiip_df['SQFT'] = cosiiip_df['Qty'] * cosiiip_df['sqft_']
    cosiiip_df['TEUS'] = cosiiip_df['Qty'] * cosiiip_df['teu_']
    cosiiip_df['CUFT'] = cosiiip_df['Qty'] * cosiiip_df['cuft_']
    cosiiip_df['Total_Cost'] = cosiiip_df['Unit_Price'] * cosiiip_df['Qty']

    # Filter rows where STONS > 0
    cosiiip_df = cosiiip_df[cosiiip_df['STONS'] > 0]

    # Select and arrange final columns
    final_columns = [
        'AssessmentNumber', 'Class_of_Supply', 'Region', 'Location_Name', 'Region_MEF_Lead',
        'COS_Type', 'POS', 'UI', 'Qty', 'POL_PKG_NSN', 'NIIN', 'NOMENCLATURE',
        'Unit_Price', 'ston_', 'sqft_', 'cuft_', 'teu_', 'CUFT', 'SQFT', 'TEUS',
        'Weight_lbs', 'STONS', 'Total_Cost'
    ]
    cosiiip_df = cosiiip_df[final_columns]

    # Convert all column names to uppercase
    cosiiip_df.columns = cosiiip_df.columns.str.upper()

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    cosiiip_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Class IIIP Embark CSV file has been uploaded to S3.")

    # Return the S3 file path
    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def lambda_handler(event, context):
    """Lambda function entry point."""
    logger.info("COS IIIP Embark transformation started.")
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
        result = cosiiip_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket)
        logger.info("COS IIIP Embark File Processed.")
        return result

    except Exception as e:
        logger.error(f"Error processing COS IIIP Embark: {str(e)}")
        return {
            'statusCode': 500,
            'body': f"Error processing COS IIIP Embark: {str(e)}"
        }
