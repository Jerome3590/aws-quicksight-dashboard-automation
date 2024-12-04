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


def cosii_vii_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket):
    """Process COS II/VII Embark data."""
    input_file_path = s3_key
    output_file_path = f'assessments/{assessment_id}/cosii_vii_embark.csv'

    # Check for the existence of the COS II/VII Embark CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("Class II/VII Embark file already exists.")
        return {
            'statusCode': 200,
            'body': 'File already exists',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Class II/VII Embark file does not exist, creating...")
        else:
            raise

    # Check for the input file
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
    cosii_vii_df = read_s3_csv(s3_client, source_bucket, object_key=input_file_path)
    
    logger.info(f"cosii_vii_df: {cosii_vii_df.columns.tolist()}")

    # Concatenate TAMCN with "_Class_II/VII" and set additional columns
    cosii_vii_df['COS_Type'] = cosii_vii_df['TAMCN'].astype(str) + "_Class_II/VII"
    cosii_vii_df['sqft_'] = cosii_vii_df['IND SQFT']
    cosii_vii_df['cuft_'] = cosii_vii_df['IND CUFT']
    cosii_vii_df['ston_'] = cosii_vii_df['IND STON']
    cosii_vii_df['mton_'] = cosii_vii_df['IND MTON']
    cosii_vii_df['teu_'] = cosii_vii_df['TEU_Equivalents']
    cosii_vii_df['Qty'] = cosii_vii_df['TE_Orig_Qty']
    cosii_vii_df['Unit_Price'] = cosii_vii_df['STANDARD_UNIT_PRICE']

    # Drop the specified columns
    columns_to_drop = [
        'MEF', 'MSE', 'TAMCN_Group', 'TAMCN', 'NIIN', 'Day', 'SQFT(C)',
        'COLLOQUIAL_NAME', 'TAM_STATUS', 'ITEM_EXIT_DATE', 'STORES_ACCOUNT_CODE', 
        'ITEM_DESIGNATOR_CODE', 'READINESS_REPORTABLE_CODE', 'PREF_NIIN_IND', 
        'PREF_NIIN_RNK', 'EQUIP_CRIT_RNK', 'CHILD_TAMCN', 'TARIFF_FCTR', 
        'CLASSII_VII_INDICATOR', 'Mission_TE', 'RD', 'CAP_Name', 'CAP_Names', 
        'ILOC', 'Unit_Name', 'Priority', 'Parent_UIC', 'AAC', 'Prepo_Enabled', 
        'isArty', 'TAMCN_5', 'IND SQFT', 'IND CUFT', 'IND STON', 'IND MTON', 
        'EMBARK', 'PO ITEM', 'LOCAL TAM', 'STANDARD UNIT PRICE', 
        'REPLACEMENT COST', 'CLASSOFSUPPLY', 'TEU_Equivalents', 'TE_Orig_Qty'
    ]
    cosii_vii_df = cosii_vii_df.drop(columns=columns_to_drop, errors='ignore')
    
    # Assign "NIIN" to UI column
    cosii_vii_df["UI"] = "NIIN"

    # Calculations
    cosii_vii_df['STONS'] = cosii_vii_df['Qty'] * cosii_vii_df['ston_']
    cosii_vii_df['Weight_lbs'] = cosii_vii_df['STONS'] / 2000
    cosii_vii_df['SQFT'] = cosii_vii_df['Qty'] * cosii_vii_df['sqft_']
    cosii_vii_df['TEUS'] = cosii_vii_df['Qty'] * cosii_vii_df['teu_']
    cosii_vii_df['CUFT'] = cosii_vii_df['Qty'] * cosii_vii_df['cuft_']
    cosii_vii_df['Total_Cost'] = cosii_vii_df['Unit_Price'] * cosii_vii_df['Qty']
    
    logger.info(f"cosii_vii_df after calc: {cosii_vii_df.columns.tolist()}")

    # Sort and select final columns
    final_columns = [
        'AssessmentNumber', 'Class_of_Supply', 'COS_Type', 'Region', 'Region_MEF_Lead',
        'Location_Name', 'UIC', 'UI', 'Unit_Price', 'DIM HEIGHT INCHES', 
        'DIM LENGTH INCHES', 'DIM WIDTH INCHES', 'ston_', 'sqft_', 'cuft_', 
        'teu_', 'POS', 'Qty', 'CUFT', 'SQFT', 'TEUS', 'Weight_lbs', 'Total_Cost'
    ]
    cosii_vii_df = cosii_vii_df[final_columns]

    # Filter rows where Weight_lbs > 0
    cosii_vii_df = cosii_vii_df[cosii_vii_df['Weight_lbs'] > 0]

    # Convert all column names to uppercase
    cosii_vii_df.columns = cosii_vii_df.columns.str.upper()

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    cosii_vii_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Class II/VII Embark CSV file has been uploaded to S3.")

    # Return the S3 file path
    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def lambda_handler(event, context):
    logger.info("COS II/VII Embark transformation started")
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
        result = cosii_vii_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket)
        logger.info("COS II/VII Embark File Processed")
        return result

    except Exception as e:
        logger.error(f"Error processing COS II/VII Embark: {str(e)}")
        return {
            'statusCode': 500,
            'body': f"Error processing COS II/VII Embark: {str(e)}"
        }
