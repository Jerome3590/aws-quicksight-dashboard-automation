import pandas as pd
import boto3
import numpy as np
import re
from io import StringIO
import logging
import json

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')
dynamodb_client = boto3.client('dynamodb')


def read_s3_csv(s3_client, bucket_name, key):
    """Utility function to read CSV from S3."""
    obj = s3_client.get_object(Bucket=bucket_name, Key=key)
    return pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')))
  

def check_file_exists(s3_client, bucket, key):
    """Check if a file exists in S3."""
    s3_client.head_object(Bucket=bucket, Key=key)
    

def cosi_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket):
    # Define file paths
    input_file_path = s3_key
    output_file_path = f'assessments/{assessment_id}/cosi_embark.csv'

    # Check for the existence of the COS I Embark CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("Class I Embark file already exists")
        return {
            'statusCode': 200,
            'body': 'File already exists',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Class I Embark file does not exist, creating...")
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
    cosi_df = read_s3_csv(s3_client, source_bucket, s3_key)

    # Process DataFrame
    cosi_df.columns = [re.sub("DOS", "POS", col) for col in cosi_df.columns]
    cosi_df = cosi_df.rename(columns={"BEYOND": "BEYOND_UNITS", "Unit of Issue": "UI", "Ration_Type": "COS_TYPE"})
    cosi_df.columns = cosi_df.columns.str.upper()

    # Drop columns and filter
    cosi_df = cosi_df.drop(columns=['CAP_NAME', 'CAP_NAMES'], errors='ignore')  
    cosi_df = cosi_df.loc[:, ~cosi_df.columns.str.contains('FEUS')]

    # Add calculations
    cosi_df['BEYOND_UNITS_PALLET_QTY'] = np.select(
        [
            cosi_df['COS_TYPE'] == "MEALS_READY_TO_EAT_MRE",
            cosi_df['COS_TYPE'].isin(["UGR_H_S_BREAKFAST", "UGR_H_S_LUNCH_DINNER", "UGR_M_BREAKFAST", "UGR_M_LUNCH_DINNER"])
        ],
        [
            cosi_df['BEYOND_UNITS'] / 48,
            cosi_df['BEYOND_UNITS'] / 8
        ],
        default=np.nan
    )

    # Further calculations and melting
    cosi_embark_df = process_cosi_data(cosi_df)

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    cosi_embark_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Class I Embark CSV file has been uploaded to S3.")

    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def process_cosi_data(cosi_df):
    """Processes COSI data with pivot and cost calculations."""
    cosi_embark_df = cosi_df.melt(
        id_vars=["ASSESSMENTNUMBER", "CLASS_OF_SUPPLY", "COS_TYPE", "REGION", "REGION_MEF_LEAD", "LOCATION_ID", "LOCATION_NAME", "UI"],
        value_vars=[col for col in cosi_df.columns if re.match(r"^(BEYOND|POS[1-3])_", col)],
        var_name="POS_METRIC",
        value_name="VALUE"
    )
    cosi_embark_df[['POS', 'METRIC']] = cosi_embark_df['POS_METRIC'].str.extract(r'^(BEYOND|POS[1-3])_(.*)')
    cosi_embark_df = cosi_embark_df.pivot(
        index=["ASSESSMENTNUMBER", "CLASS_OF_SUPPLY", "COS_TYPE", "REGION", "REGION_MEF_LEAD", "LOCATION_ID", "LOCATION_NAME", "UI", "POS"],
        columns="METRIC",
        values="VALUE"
    ).reset_index()
    
    cosi_embark_df = cosi_embark_df.rename(columns={"UNITS": "QTY", 
      "UNITS_PALLET_QTY": "PALLETS", 
      "UNITS_PALLET_TOT_CUFT": "CUFT", 
      "UNITS_PALLET_TOT_TEUS": "TEUS",
      "UNITS_PALLET_TOT_WT(LBS)": "WEIGHT_LBS"}
    )

    # Define cost mapping
    cost_dict = {
        "MEALS_READY_TO_EAT_MRE": 119.03,
        "UGR_H_S_BREAKFAST": 403.90,
        "UGR_H_S_LUNCH_DINNER": 364.91,
        "UGR_M_BREAKFAST": 273.33,
        "UGR_M_LUNCH_DINNER": 273.33
    }

    # Add cost calculations
    cosi_embark_df['TOTAL_COST'] = cosi_embark_df.apply(
        lambda row: row['QTY'] * cost_dict.get(row['COS_TYPE'], 0),
        axis=1
    )

    return cosi_embark_df


def lambda_handler(event, context):
    logger.info("COS I Embark transformation started")
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        target_bucket = '{bucket_name}'
        source_bucket = '{bucket_name}'
        s3_key = event.get('s3_key', '')
        assessment_id = event.get('assessment_id', '')

        if not s3_key or not assessment_id:
            raise ValueError("Missing required keys in event: 's3_key', 'assessment_id'")

        result = cosi_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket)
        logger.info("COS I Embark File Processed")
        return result

    except Exception as e:
        logger.error(f"Error processing COS I Embark: {str(e)}")
        return {
            'statusCode': 500,
            'body': f"Error processing COS I Embark: {str(e)}"
        }
