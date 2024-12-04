import pandas as pd
import boto3
import numpy as np
from io import StringIO
import logging
import json
import shutil

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')
dynamodb_client = boto3.client('dynamodb')


def read_s3_csv(s3_client, bucket_name, object_key, dtype=None, usecols=None):
    """Utility function to read CSV from S3."""
    obj = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    return pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')), dtype=dtype, usecols=usecols)


def check_file_exists(s3_client, bucket, key):
    """Check if a file exists in S3 with retry logic."""
    s3_client.head_object(Bucket=bucket, Key=key)


def cosix_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket):
    # Local files
    source_path = '/var/task/classix_dimensional_data.csv'
    destination_path = '/tmp/classix_dimensional_data.csv'

    # Copy dimensional data lookup file to /tmp directory
    shutil.copyfile(source_path, destination_path)

    # Define file paths
    input_file_path = s3_key
    output_file_path = f'assessments/{assessment_id}/cosix_embark.csv'

    # Check for the existence of the COSIX Embark CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("Class IX Embark file already exists")
        return {
            'statusCode': 200,
            'body': 'File already exists',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Class IX Embark file does not exist, creating...")
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
    columns_to_keep = [
        "AssessmentNumber", "Class_of_Supply", "Region", "MEF", "FIE", "Location Name",
        'SECREP Flag', 'Battery Flag',
        "TAMCN_NIIN", "TAMCN_of_End_Item", "NIIN_of_End_Item", "COLLOQUIAL_NAME",
        "TAMCN_NIIN_QTY_FIE", "TAMCN_NIIN_QTY_POS1", "TAMCN_NIIN_QTY_POS2", "TAMCN_NIIN_QTY_POS3",
        "TAMCN_NIIN_QTY_BEYOND", "FSC of Part Required", "NIIN of Part Required",
        "Nomenclature of Part Required", "UNIT_OF_ISSUE",
        "STANDARD UNIT PRICE", "Required_FIE", "Required_POS1", "Required_POS2", "Required_POS3",
        "Required_BEYOND", "Total_Required_Less_FIE", "Total_Required_Selected_Confidence",
        "Value_FIE", "Value_POS1", "Value_POS2", "Value_POS3", "Value_BEYOND",
        "Total_Value_Less_FIE", "Total_Value_Selected_Confidence", "IND STON", "IND SQFT",
        "DIM LENGTH INCHES", "DIM WIDTH INCHES", "DIM HEIGHT INCHES"
    ]

    # Define data types for reading the CSV
    schema = {
        'AssessmentNumber': 'str',
        'Class_of_Supply': 'str',
        'Region': 'str',
        'SECREP Flag': 'bool',
        'Battery Flag': 'bool',
        'MEF': 'str',
        'FIE': 'str',
        'Location Name': 'str',
        'TAMCN_NIIN': 'str',
        'TAMCN_of_End_Item': 'str',
        'NIIN_of_End_Item': 'str',
        'COLLOQUIAL_NAME': 'str',
        'FSC of Part Required': 'str',
        'NIIN of Part Required': 'str',
        'Nomenclature of Part Required': 'str',
        'UNIT_OF_ISSUE': 'str',
        'STANDARD UNIT PRICE': 'float64',
        'Required_FIE': 'int64',
        'Required_POS1': 'int64',
        'Required_POS2': 'int64',
        'Required_POS3': 'int64',
        'Required_BEYOND': 'int64',
        'Total_Required_Less_FIE': 'float64',
        'Total_Required_Selected_Confidence': 'float64',
        'Value_FIE': 'float64',
        'Value_POS1': 'float64',
        'Value_POS2': 'float64',
        'Value_POS3': 'float64',
        'Value_BEYOND': 'float64',
        'Total_Value_Less_FIE': 'float64',
        'Total_Value_Selected_Confidence': 'float64',
        'IND STON': 'float64',
        'IND SQFT': 'float64',
        'DIM LENGTH INCHES': 'float64',
        'DIM WIDTH INCHES': 'float64',
        'DIM HEIGHT INCHES': 'float64'
    }

    df1 = read_s3_csv(s3_client, source_bucket, input_file_path, dtype=schema, usecols=columns_to_keep)
    
    logger.info("df1 columns from AWS S3: f'{df1.columns.tolist()")

    # Rename columns using a dictionary
    rename_dict1 = {
        'Location Name': 'Location_Name',
        'COLLOQUIAL_NAME': 'Colloquial Name',
        'UNIT_OF_ISSUE': 'UI',
        'STANDARD UNIT PRICE': 'Unit_Price',
        "Nomenclature of Part Required": "Nomenclature",
        "NIIN of Part Required": "NIIN"
    }
    
    df1.rename(columns=rename_dict1, inplace=True)

    # Process SECREP and Battery Flags to boolean
    df1['SECREP Flag'] = df1['SECREP Flag'].astype(str).str.upper().map({'TRUE': True, 'FALSE': False})
    df1['Battery Flag'] = df1['Battery Flag'].astype(str).str.upper().map({'TRUE': True, 'FALSE': False})

    # Update Class_of_Supply based on Battery Flag and SECREP Flag conditions
    df1['Class_of_Supply'] = np.where(
        df1['Battery Flag'] == True, 'IX C (B)',
        np.where(df1['SECREP Flag'] == True, 'IX C (S)', 'IX C')
    )

    df1.drop(columns=['SECREP Flag', 'Battery Flag'], inplace=True)
    
    logger.info(f"df1 columns after first transform: {df1.columns.tolist()}")

    # Reading the file from /tmp directory using pandas
    try:
        df2 = pd.read_csv(destination_path)
    except FileNotFoundError:
        return {
            'statusCode': 404,
            'body': json.dumps('File not found.')
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f'An error occurred: {str(e)}')
        }
        
    logger.info(f"df2 columns from /tmp: {df2.columns.tolist()}")

    # Rename columns in df2 and retain distinct rows based on NIIN and Nomenclature
    df2 = df2.rename(columns={
        "UNIT_PRICE": "Unit_Price",
        "NOMENCLATURE": "Nomenclature"
    }).drop_duplicates(subset=["NIIN", "Nomenclature"])
    
     # Convert all column names to uppercase
    df2.columns = df2.columns.str.upper()
    
    logger.info(f"df2 columns after first transform: {df2.columns.tolist()}")

    # Join the DataFrames on NIIN
    cosix_df = pd.merge(df1, df2, on=['NIIN'], suffixes=('_left', '_right'))
    
    logger.info(f"cosix_df columns after join: {cosix_df.columns.tolist()}")

    # Melt the DataFrame to long format for *_POS* and *_BEYOND columns
    pos_columns = [col for col in cosix_df.columns if "_POS" in col or "_BEYOND" in col]
    df_long = cosix_df.melt(
        id_vars=[col for col in cosix_df.columns if col not in pos_columns],
        value_vars=pos_columns,
        var_name="variable",
        value_name="Requirement"
    )
    
    logger.info(f"df_long columns after melt: {df_long.columns.tolist()}")

    # Extract base and POS indicator from `variable`
    df_long[['base', 'POS']] = df_long['variable'].str.extract(r"(.+?)_(POS[0-9]+|BEYOND)")
    df_long.drop(columns=['variable'], inplace=True)

    # Replace 0.00 with 0.01 in df_long['DSS_CUBE']
    df_long['DSS_CUBE'] = np.where(df_long['DSS_CUBE'] == 0.00, 0.01, df_long['DSS_CUBE'])

    # Calculate CUFT (cubic feet) directly in df_long for each NIIN
    df_long['CUFT'] = np.where(
        (df_long['DIM LENGTH INCHES'] == 9999) | (df_long['DIM LENGTH INCHES'] == 0) |
        (df_long['DIM WIDTH INCHES'] == 9999) | (df_long['DIM WIDTH INCHES'] == 0) |
        (df_long['DIM HEIGHT INCHES'] == 9999) | (df_long['DIM HEIGHT INCHES'] == 0),
        df_long['DSS_CUBE'],
        np.maximum(
            (df_long['DIM LENGTH INCHES'] / 12) *
            (df_long['DIM WIDTH INCHES'] / 12) *
            (df_long['DIM HEIGHT INCHES'] / 12),
            df_long['DSS_CUBE']
        )
    )

    # Calculate SQFT (square feet) directly in df_long for each NIIN
    df_long['SQFT'] = np.where(
        (df_long['DIM LENGTH INCHES'] == 9999) | (df_long['DIM LENGTH INCHES'] == 0) |
        (df_long['DIM WIDTH INCHES'] == 9999) | (df_long['DIM WIDTH INCHES'] == 0),
        0,
        (df_long['DIM LENGTH INCHES'] / 12) * (df_long['DIM WIDTH INCHES'] / 12)
    )

    df_long['Weight_lbs'] = df_long['DSS_WEIGHT'] * df_long['Requirement']

    # Perform FIE-specific calculations for each POS level
    df_long['FIE_CUFT'] = df_long['CUFT'] * df_long['Required_FIE'] 
    df_long['FIE_SQFT'] = df_long['SQFT'] * df_long['Required_FIE']
    df_long['FIE_Weight_lbs'] = df_long['DSS_WEIGHT'] * df_long['Required_FIE']
    
    logger.info(f"df_long columns after calculations: {df_long.columns.tolist()}")

    rename_dict2 = {
        'TAMCN_of_End_Item_left': 'TAMCN_of_End_Item',
        'Colloquial Name': 'Colloquial_Name',
        'Total_Value_Less_FIE':'Total_Cost_Less_FIE',
        'Value_FIE':'Cost_FIE',
        'Total_Value_Selected_Confidence': 'Total_Cost',
        'base': 'Feature',
        'Unit_Price_left': 'Unit_Price',
        'UI_left': 'UI'
    }
    df_long.rename(columns=rename_dict2, inplace=True)
    
    logger.info(f"df_long columns after rename: {df_long.columns.tolist()}")

    # Select and order columns in df_long
    cosix_df_final = df_long[['AssessmentNumber', 'Class_of_Supply', 'Region', 'MEF', 'FIE', 'Location_Name',
                        'TAMCN_NIIN', 'TAMCN_of_End_Item', 'NIIN_of_End_Item',
                        'Colloquial_Name', 'TAMCN_NIIN_QTY_FIE', 'FSC of Part Required',
                        'NIIN', 'Nomenclature', 'UI', 'Unit_Price',
                        'DIM LENGTH INCHES', 'DIM WIDTH INCHES', 'DIM HEIGHT INCHES',
                        'DSS_WEIGHT', 'DSS_CUBE', 'Feature',
                        'Required_FIE', 'FIE_CUFT', 'FIE_SQFT', 'FIE_Weight_lbs', 'Cost_FIE',
                        'POS', 'Requirement', 'CUFT', 'SQFT', 'Weight_lbs', 'Total_Cost_Less_FIE', 'Total_Cost']]
                        
    # Convert all column names to uppercase
    cosix_df_final.columns = cosix_df_final.columns.str.upper()
    
    logger.info(f"cosix_df_final columns: {cosix_df_final.columns.tolist()}")

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    cosix_df_final.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Class IX Embark CSV file has been uploaded to S3.")

    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def lambda_handler(event, context):
    logger.info("COS IX Embark transformation started")
    logger.info(f"Received event: {json.dumps(event)}")

    target_bucket = '{bucket_name}'
    source_bucket = '{bucket_name}'
    s3_key = event['s3_key']
    assessment_id = event['assessment_id']

    result = cosix_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket)

    logger.info("COS IX Embark File Processed")
    return result
