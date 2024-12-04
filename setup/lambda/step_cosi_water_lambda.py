import pandas as pd
import boto3
from io import StringIO
import logging
import json
import numpy as np
import re

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')


def read_s3_csv(s3_client, bucket_name, key):
    """Utility function to read CSV from S3."""
    obj = s3_client.get_object(Bucket=bucket_name, Key=key)
    return pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')))


def check_file_exists(s3_client, bucket, key):
    """Check if a file exists in S3."""
    s3_client.head_object(Bucket=bucket, Key=key)


def cosi_water_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket):
    """Process Class I Water Embark Data."""
    output_file_path = f'assessments/{assessment_id}/cosi_water_embark.csv'

    # Check for the existence of the COS I Water Embark CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("Class I Water Embark file already exists.")
        return {
            'statusCode': 200,
            'body': 'File already exists',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Class I Water Embark file does not exist, creating...")
        else:
            raise

    # Check for the input file to be available
    try:
        check_file_exists(s3_client, source_bucket, s3_key)
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.error(f"Input file {s3_key} does not exist.")
            return {
                'statusCode': 404,
                'body': f'Input file {s3_key} does not exist.'
            }
        else:
            raise

    # Read data from S3
    cosi_water_df = read_s3_csv(s3_client, source_bucket, s3_key)
    
    logger.info(f"cosi_water_df: {cosi_water_df.columns.tolist()}")
    
    # Log DataFrame columns before renaming
    logger.info(f"DataFrame columns before renaming: {cosi_water_df.columns.tolist()}")

    # Define the identifier and value columns
    id_vars = ["AssessmentNumber", "Class_of_Supply", "Region", "Location_Name", "Location_ID", "Region_MEF_Lead", "UIC", "POS"]
    value_vars = ["Potable_Rqmt", "NonPotable_Rqmt", "Drinking_Rqmt"]

    # Pivot to long format
    cosi_water_df_long = cosi_water_df.melt(id_vars=id_vars, value_vars=value_vars, var_name="COS_Type", value_name="Units")

    logger.info(f"cosi_water_df_long after melt: {cosi_water_df_long.columns.tolist()}")

    # Perform calculations conditionally for 'Drinking_Rqmt' only
    cosi_water_df_long['Pallets'] = np.where(cosi_water_df_long['COS_Type'] == "Drinking_Rqmt", cosi_water_df_long['Units'] / 50 * (1 + 0.02 * 0.04), np.nan)
    cosi_water_df_long['CUFT'] = np.where(cosi_water_df_long['COS_Type'] == "Drinking_Rqmt", cosi_water_df_long['Pallets'] * 56.11, np.nan)
    cosi_water_df_long['Weight_lbs'] = np.where(cosi_water_df_long['COS_Type'] == "Drinking_Rqmt", cosi_water_df_long['Pallets'] * 1700, np.nan)
    cosi_water_df_long['TEUS'] = np.where(cosi_water_df_long['COS_Type'] == "Drinking_Rqmt", (cosi_water_df_long['Pallets'] / 16) * (1 + 0.063), np.nan)
    cosi_water_df_long['Total_Cost'] = np.where(cosi_water_df_long['COS_Type'] == "Drinking_Rqmt", cosi_water_df_long['Units'] * 3.05, np.nan)
    cosi_water_df_long['UI'] = np.where(cosi_water_df_long['COS_Type'] == "Drinking_Rqmt", "Case", "Gals")

    # Fill all NAs with zeros
    cosi_water_df_long[['Pallets', 'CUFT', 'Weight_lbs', 'TEUS', 'Total_Cost']] = cosi_water_df_long[['Pallets', 'CUFT', 'Weight_lbs', 'TEUS', 'Total_Cost']].fillna(0)

    # Rename columns
    cosi_water_df_long = cosi_water_df_long.rename(columns={'Units': 'Qty'})
    
    logger.info(f"cosi_water_df_long after rename: {cosi_water_df_long.columns.tolist()}")

    # Select final columns
    final_columns = [
        "AssessmentNumber", "Class_of_Supply", "COS_Type", "Region", "Region_MEF_Lead", "Location_Name",
        "Location_ID", "UIC", "UI", "POS", "Qty", "Pallets", "CUFT", "Weight_lbs", "TEUS", "Total_Cost"
    ]
    cosi_water_df_final = cosi_water_df_long[final_columns]

    # Filter rows where Weight_lbs > 0
    cosi_water_df_final = cosi_water_df_final[cosi_water_df_final['Weight_lbs'] > 0]

    # Convert all column names to uppercase
    cosi_water_df_final.columns = cosi_water_df_final.columns.str.upper()

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    cosi_water_df_final.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Class I Water Embark CSV file has been uploaded to S3.")

    # Return the S3 file path
    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def lambda_handler(event, context):
    logger.info("COS I Water Embark transformation started")
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
        result = cosi_water_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket)
        logger.info("COS I Water Embark File Processed")
        return result

    except Exception as e:
        logger.error(f"Error processing COS I Water Embark: {str(e)}")
        return {
            'statusCode': 500,
            'body': f"Error processing COS I Water Embark: {str(e)}"
        }
