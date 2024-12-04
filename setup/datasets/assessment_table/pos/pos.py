import pandas as pd
import re
import boto3
from io import StringIO


def read_s3_csv(s3_client, bucket_name, object_key):
    """Utility function to read CSV from S3."""
    obj = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    return pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')))
  

def get_POS(bucket_name, assessment_id):
    s3_client = boto3.client('s3')
    
    # Define file paths
    input_file_path1 = f'assessments/{assessment_id}/cos-calculators/AssessmentTable.csv'
    input_file_path2 = f'assessments/{assessment_id}/cos-calculators/AssessmentParameterDataMapTable.csv'
    output_file_path = f'assessments/{assessment_id}/cos-calculators/pos.csv'

    # Check for the existence of the POS.CSV file
    try:
        s3_client.head_object(Bucket=bucket_name, Key=output_file_path)
        print("POS.csv file already exists")
        return
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            print("POS.csv file does not exist, creating...")
        else:
            raise

    # Read data from S3
    df1 = read_s3_csv(s3_client, bucket_name, input_file_path1)
    df2 = read_s3_csv(s3_client, bucket_name, input_file_path2)
    
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
    pos_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=bucket_name, Key=output_file_path, Body=csv_buffer.getvalue())
    print("POS.csv file has been uploaded to S3.")
