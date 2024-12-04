import pandas as pd
import boto3
from io import StringIO
import logging
import json
import re

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
    

def read_and_clean_s3_csv(s3_client, bucket_name, object_key, retain_columns=None):
    """Read CSV from S3 and clean column names."""
    if retain_columns is None:
        retain_columns = []
    
    df = read_s3_csv(s3_client, bucket_name, object_key)
    
    # Replace '.' with '_' in column names
    df.columns = df.columns.str.replace('.', '_')
    logger.info(f"Columns after replacing '.' with '_': {df.columns.tolist()}")

    # Apply casing rules for columns
    df.columns = [
        col.upper() if col in ['POS', 'DOS', 'UIC', 'MSE', 'MEF'] else col if col in retain_columns else col.capitalize()
        for col in df.columns
    ]
    logger.info(f"Columns after applying casing rules: {df.columns.tolist()}")
    
    return df


def get_Force_Flow(s3_client, bucket_source, assessment_id, target_bucket, retain_columns=None):
  
    if retain_columns is None:
        retain_columns = []
        
    # Define file paths
    input_file_path1 = f'assessments/{assessment_id}/cos-calculators/AssessmentTable.csv'
    input_file_path2 = f'assessments/{assessment_id}/POS.csv'
    input_file_path3 = f'assessments/{assessment_id}/cos-calculators/cos-i-subsistence/output/RD_I_PaxLocationAll.csv'
    output_file_path = f'assessments/{assessment_id}/ForceFlow_joined.csv'

    logger.info(f"Input file paths being checked: {bucket_source}/{input_file_path1}, {target_bucket}/{input_file_path2}, {bucket_source}/{input_file_path3}")

    # Check for the existence of the ForceFlow_joined.csv file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("ForceFlow_joined.csv file already exists")
        return {
            'statusCode': 200,
            'body': 'File already exists, no action taken.',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("ForceFlow_joined.csv file does not exist, creating...")
        else:
            raise

    # Check for files 
    try:
        check_file_exists(s3_client, bucket_source, input_file_path1)
        check_file_exists(s3_client, target_bucket, input_file_path2)
        check_file_exists(s3_client, bucket_source, input_file_path3)
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.error("One or more of the input files do not exist.")
            return {
                'statusCode': 404,
                'body': 'One or more of the input files do not exist.'
            }
        else:
            raise
            
    # Read data from S3
    df1 = read_and_clean_s3_csv(s3_client, bucket_source, input_file_path1, retain_columns)
    df2 = read_and_clean_s3_csv(s3_client, target_bucket, input_file_path2, retain_columns)
    df3 = read_and_clean_s3_csv(s3_client, bucket_source, input_file_path3, retain_columns)

    # Merge dataframes
    df4 = pd.merge(df3, df1, on='AssessmentNumber', how='left')
    df5 = pd.merge(df4, df2, left_on='DOS_Period', right_on='DOS', how='left')

    # Keep specific columns
    df5 = df5[['AssessmentNumber', 'Class_of_Supply', 'UIC', 'Flowin', 'Cumflow',
               'Location_Name', 'Location_ID', 'Region', 'Region_MEF_Lead', 'MEF', 'Service',
               'MSE', 'Day', 'Feed_Plan_ID', 'MEALS_READY_TO_EAT_MRE', 'UGR_H_S_BREAKFAST',
               'UGR_H_S_LUNCH_DINNER', 'UGR_M_BREAKFAST', 'UGR_M_LUNCH_DINNER', 'DOS_Period',
               'POS', 'DOS', 'Period', 'Sort_Index', 'OperationDuration']]
    
    df5['POS'] = df5['POS'].str.upper()
    
    daily_Inflow = df5.groupby('Day')['Flowin'].sum().reset_index()
    
    daily_Inflow.rename(columns={'Flowin': 'daily_Inflow'}, inplace=True)

    daily_Inflow['Cumulative_Daily_Inflow'] = daily_Inflow['daily_Inflow'].cumsum()

    # Merge the cumulative daily inflow back into the original dataframe
    df5 = pd.merge(df5, daily_Inflow[['Day', 'daily_Inflow', 'Cumulative_Daily_Inflow']], on='Day', how='left')

    
    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    df5.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Joined ForceFlow CSV file has been uploaded to S3.")

    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def get_joins(s3_client, bucket_source, assessment_id, target_bucket, retain_columns):
    # Define file paths
    input_file_path1 = f'assessments/{assessment_id}/POS.csv'
    input_file_path2 = f'assessments/{assessment_id}/cos-calculators/cos-i-water/output/RD_IW_PaxLocationAll.csv'
    input_file_path3 = f'assessments/{assessment_id}/cos-calculators/cos-i-water/output/RD_IW_Ration_Costs.csv'
    input_file_path4 = f'assessments/{assessment_id}/cos-calculators/cos-i-subsistence/output/RD_I_POS_Location.csv'
    input_file_path5 = f'assessments/{assessment_id}/cos-calculators/cos-i-subsistence/output/RD_I_Ration_Costs.csv'
    output_file_path2 = f'assessments/{assessment_id}/RD_IW_PaxLocationAll_joined.csv'
    output_file_path3 = f'assessments/{assessment_id}/RD_IW_Ration_Costs_joined.csv'
    output_file_path4 = f'assessments/{assessment_id}/RD_I_POS_Location_joined.csv'
    output_file_path5 = f'assessments/{assessment_id}/RD_I_Ration_Costs_joined.csv'

    logger.info(f"Input file paths being checked: {bucket_source}/{input_file_path1}, {target_bucket}/{input_file_path2}, {bucket_source}/{input_file_path3}, {bucket_source}/{input_file_path4}, {bucket_source}/{input_file_path5}")

    # Check for the existence of the joined files
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path2)
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path3)
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path4)
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path5)
        logger.info("Files already exist")
        return
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Files do not exist, creating...")
        else:
            raise

    # Check for files 
    try:
        check_file_exists(s3_client, bucket_source, input_file_path1)
        check_file_exists(s3_client, target_bucket, input_file_path2)
        check_file_exists(s3_client, bucket_source, input_file_path3)
        check_file_exists(s3_client, bucket_source, input_file_path4)
        check_file_exists(s3_client, bucket_source, input_file_path5)
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.error("One or more of the input files do not exist.")
            return {
                'statusCode': 404,
                'body': 'One or more of the input files do not exist.'
            }
        else:
            raise
            
    # Read data from S3
    df1 = read_and_clean_s3_csv(s3_client, bucket_source, input_file_path1, retain_columns)
    df2 = read_and_clean_s3_csv(s3_client, target_bucket, input_file_path2, retain_columns)
    df3 = read_and_clean_s3_csv(s3_client, bucket_source, input_file_path3, retain_columns)
    df4 = read_and_clean_s3_csv(s3_client, bucket_source, input_file_path4, retain_columns)
    df5 = read_and_clean_s3_csv(s3_client, bucket_source, input_file_path5, retain_columns) 
    
    # Log DataFrame columns before renaming
    logger.info(f"DataFrame columns before renaming: {df2.columns.tolist()}")
    
    # Rename columns to match expected output
    rename_dict = {
        'Potable_rqmt': 'Potable_Rqmt',
        'Non-potable': 'NonPotable',
        'Non-potable_rqmt': 'NonPotable_Rqmt',
        'Total water': 'Total_Water',
        'Total_rqmt': 'Total_Rqmt',
        'Drinking_rqmt': 'Drinking_Rqmt'
    }
    
    # Rename the columns in the DataFrame
    df2.rename(columns=rename_dict, inplace=True)
    df2 = df2.drop(columns=['V7', 'V8', 'V9'], errors='ignore')
    
    # Create new columns that sum the aggregations by day for Inflow, Potable_Rqmt, NonPotable_Rqmt, and Drinking_Rqmt
    df2['daily_Inflow'] = df2.groupby('Day')['Flowin'].transform('sum')
    df2['daily_Potable_Rqmt'] = df2.groupby('Day')['Potable_Rqmt'].transform('sum')
    df2['daily_NonPotable_Rqmt'] = df2.groupby('Day')['NonPotable_Rqmt'].transform('sum')
    df2['daily_Drinking_Rqmt'] = df2.groupby('Day')['Drinking_Rqmt'].transform('sum')
    
    
    # Create a new calculated column that is a sum of daily_NonPotable_Rqmt and daily_Potable_Rqmt
    df2['daily_Total_Potable_NonPotable_Rqmt'] = df2['daily_Potable_Rqmt'] + df2['daily_NonPotable_Rqmt']
    
    # Join dataframes
    df6 = pd.merge(df2, df1, how='left', left_on='DOS_Period', right_on='DOS')
    df7 = pd.merge(df3, df1, how='left', left_on='DOS_Period', right_on='DOS')
    df8 = pd.merge(df4, df1, how='left', left_on='DOS_Period', right_on='DOS')
    df9 = pd.merge(df5, df1, how='left', left_on='DOS_Period', right_on='DOS')
    
    # Convert and upload joined files to S3
    for idx, (df, output_path) in enumerate(zip([df6, df7, df8, df9], [output_file_path2, output_file_path3, output_file_path4, output_file_path5])):
        csv_buffer = StringIO()
        df.to_csv(csv_buffer, index=False)
        s3_client.put_object(Bucket=target_bucket, Key=output_path, Body=csv_buffer.getvalue())
        logger.info(f"{output_path} file has been uploaded to S3.")

    return {
        'statusCode': 200,
        'body': 'Files successfully joined and uploaded.'
    }


def lambda_handler(event, context):
    logger.info("Force Flow Join transformation started")
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        target_bucket = event['source_bucket']
        bucket_source = 'wrsa-dev.canallc.com'
        assessment_id = event['assessment_id']
        
        # List of columns to retain their original capitalization
        retain_columns = ['AssessmentNumber', 'Class_of_Supply',
                   'Location_Name', 'Location_ID', 'Region_MEF_Lead',
                   'Feed_Plan_ID', 'MEALS_READY_TO_EAT_MRE', 'UGR_H_S_BREAKFAST',
                   'UGR_H_S_LUNCH_DINNER', 'UGR_M_BREAKFAST', 'UGR_M_LUNCH_DINNER', 
                   'DOS_Period', 'Sort_Index', 'OperationDuration', 'Ration_Type', 
                   'Ration_Cost', 'UI_Count', 'UI_Cost', 'CAP_Name', 'CAP_Names', 
                   'Total_UI_Cost']

        ff_response = get_Force_Flow(s3_client, bucket_source, assessment_id, target_bucket, retain_columns)
        if not ff_response:
            logger.error("get_Force_Flow returned None or an invalid response.")
            return {
                'statusCode': 500,
                'body': 'Error: get_Force_Flow failed'
            }

        join_response = get_joins(s3_client, bucket_source, assessment_id, target_bucket, retain_columns)
        if not join_response:
            logger.error("get_joins returned None or an invalid response.")
            return {
                'statusCode': 500,
                'body': 'Error: get_joins failed'
            }

        logger.info("Join Files Processed")
        
        return {
            'statusCode': 200,
            'body': join_response.get('body', 'No body returned'),
            's3_file_path': ff_response.get('s3_file_path', 'No path returned')
        }

    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        return {
            'statusCode': 500,
            'body': f"Error: {str(e)}"
        }

