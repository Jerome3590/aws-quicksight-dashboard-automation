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


def read_s3_csv(s3_client, bucket_name, object_key, dtype=None):
    """Utility function to read CSV from S3."""
    obj = s3_client.get_object(Bucket=bucket_name, Key=object_key)
    return pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')), dtype=dtype)


def check_file_exists(s3_client, bucket, key):
    """Check if a file exists in S3."""
    s3_client.head_object(Bucket=bucket, Key=key)


def cosvi_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket):
    """Process COS VI Embark data."""
    # Define data types for reading the CSV
    schema = {
        'AssessmentNumber': 'str',
        'Class_of_Supply': 'str',
        'HCP_Type': 'str',
        'DESCRIPTION': 'str',
        'TAMCN': 'str',
        'FSC': 'str',
        'NIIN': 'str',
        'NSN': 'str',
        'TPFDD_TAMCN': 'str',
        'TPFDD_NSN': 'str',
        'Description': 'str',
        'Region': 'str',
        'Region_MEF_Lead': 'str',
        'CAP_Name': 'str',
        'CAP_Names': 'str',
        'Location_ID': 'str',
        'Location_Name': 'str',
        'Unit of Issue': 'str',
        'DOS1_Units': 'float64',
        'DOS2_Units': 'float64',
        'DOS3_Units': 'float64',
        'DOS1_Units_NIIN_Qty': 'float64',
        'DOS2_Units_NIIN_Qty': 'float64',
        'DOS3_Units_NIIN_Qty': 'float64',
        'DOS1_Units_NIIN_Qty_Pallet_Qty': 'float64',
        'DOS2_Units_NIIN_Qty_Pallet_Qty': 'float64',
        'DOS3_Units_NIIN_Qty_Pallet_Qty': 'float64',
        'DOS1_Units_NIIN_Qty_Pallet_Tot_CuFT': 'float64',
        'DOS2_Units_NIIN_Qty_Pallet_Tot_CuFT': 'float64',
        'DOS3_Units_NIIN_Qty_Pallet_Tot_CuFT': 'float64',
        'DOS1_Units_NIIN_Qty_Pallet_Tot_Wt(lbs)': 'float64',
        'DOS2_Units_NIIN_Qty_Pallet_Tot_Wt(lbs)': 'float64',
        'DOS3_Units_NIIN_Qty_Pallet_Tot_Wt(lbs)': 'float64',
        'DOS1_Units_NIIN_Qty_Pallet_Tot_TEUs': 'float64',
        'DOS2_Units_NIIN_Qty_Pallet_Tot_TEUs': 'float64',
        'DOS3_Units_NIIN_Qty_Pallet_Tot_TEUs': 'float64',
        'DOS1_Units_NIIN_Qty_Pallet_Tot_FEUs': 'float64',
        'DOS2_Units_NIIN_Qty_Pallet_Tot_FEUs': 'float64',
        'DOS3_Units_NIIN_Qty_Pallet_Tot_FEUs': 'float64',
    }

    # Define file paths
    input_file_path = s3_key
    output_file_path = f'assessments/{assessment_id}/cosvi_embark.csv'

    # Check for the existence of the COS VI Embark CSV file
    try:
        s3_client.head_object(Bucket=target_bucket, Key=output_file_path)
        logger.info("Class VI Embark file already exists.")
        return {
            'statusCode': 200,
            'body': 'File already exists',
            's3_file_path': f's3://{target_bucket}/{output_file_path}'
        }
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            logger.info("Class VI Embark file does not exist, creating...")
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
    cosvi_df = read_s3_csv(s3_client, source_bucket, object_key=input_file_path, dtype=schema)
    
    logger.info(f"cosvi_df: {cosvi_df.columns.tolist()}")

    # Define identifier and value columns
    id_vars = ["AssessmentNumber", "Class_of_Supply", "Region", "Location_ID", "Location_Name", "HCP_Type", "NIIN", "Region_MEF_Lead"]
    value_vars = [col for col in cosvi_df.columns if col.startswith("POS") or "NIIN_Qty" in col]

    # Melt the dataframe to long format
    cosvi_df_long = pd.melt(cosvi_df, id_vars=id_vars, value_vars=value_vars, var_name="Metric", value_name="Value")
    
    logger.info(f"cosvi_df_long after melt: {cosvi_df_long.columns.tolist()}")
    logger.info(f"Unique metrics in cosvi_df_long['Metric']: {cosvi_df_long['Metric'].unique()}")

    # Log unique Metric values before transformation
    logger.info(f"Unique metrics in cosvi_df_long['Metric']: {cosvi_df_long['Metric'].unique().tolist()}")
    
    # Check if the 'Metric' column exists in cosvi_df_long
    if "Metric" not in cosvi_df_long.columns:
        raise KeyError("'Metric' column is missing in cosvi_df_long.")
    
    # Extract POS and Metric_Type from Metric
    cosvi_df_long["POS"] = cosvi_df_long["Metric"].str.extract(r"(POS\d|DOS\d)")
    
    # Check for any null values in 'POS' after extraction
    if cosvi_df_long["POS"].isnull().any():
        logger.warning("Null values found in 'POS' after extraction. Check the 'Metric' column for unexpected patterns.")
        logger.info(f"Rows with null 'POS':\n{cosvi_df_long[cosvi_df_long['POS'].isnull()]}")
    
    # Transform Metric_Type
    cosvi_df_long["Metric_Type"] = cosvi_df_long["Metric"].str.replace(
        r"^(DOS\d_Units_NIIN_|POS\d_Units_NIIN_|POS\d_Units_|Pallet_|POS\d_)", "", regex=True
    )
    
    # Log unique Metric_Type values after transformation
    logger.info(f"Unique Metric_Types after transformation: {cosvi_df_long['Metric_Type'].unique().tolist()}")
    
    # Check for any null or empty Metric_Type values
    if cosvi_df_long["Metric_Type"].isnull().any():
        logger.warning("Null values found in 'Metric_Type' after transformation.")
        logger.info(f"Rows with null 'Metric_Type':\n{cosvi_df_long[cosvi_df_long['Metric_Type'].isnull()]}")
    if (cosvi_df_long["Metric_Type"] == "").any():
        logger.warning("Empty string values found in 'Metric_Type' after transformation.")
    

    logger.info(f"Unique Metric_Types before pivot: {cosvi_df_long['Metric_Type'].unique()}")

    # Pivot the data back to wide format
    cosvi_df_final = cosvi_df_long.pivot_table(
        index=["AssessmentNumber", "Class_of_Supply", "Region", "Location_ID", "Location_Name", "Region_MEF_Lead", "POS", "HCP_Type", "NIIN"],
        columns="Metric_Type",
        values="Value",
        aggfunc="first"
    ).reset_index()
    
    logger.info(f"cosvi_df_final after pivot: {cosvi_df_final.columns.tolist()}")

    # Rename columns
    cosvi_df_final.rename(columns={
        "HCP_Type": "COS_Type",
        "Units_NIIN_Qty": "Qty",
        "Qty_Pallet_Qty": "Pallets",
        "Qty_Pallet_Tot_Wt(lbs)": "Weight_lbs",
        "Qty_Pallet_Tot_CuFT": "CUFT",
        "Qty_Pallet_Tot_TEUs": "TEUS",
        "Qty_Pallet_Tot_FEUs": "FEUS"
    }, inplace=True)
    
    # Assign 'Box' to the 'UI' column
    cosvi_df_final['UI'] = 'Box'
    
    logger.info(f"cosvi_df_final after rename: {cosvi_df_final.columns.tolist()}")

    # Add Total_Cost column with conditional logic
    cosvi_df_final["Total_Cost"] = np.select(
        [
            cosvi_df_final["NIIN"].astype(str).isin(["13689154", "013689154"]),
            cosvi_df_final["NIIN"].astype(str).isin(["13689155", "013689155"]),
            cosvi_df_final["NIIN"].astype(str).isin(["14877488", "014877488"])
        ],
        [
            cosvi_df_final["Qty"] * 240.58,
            cosvi_df_final["Qty"] * 50.95,
            cosvi_df_final["Qty"] * 68.38
        ],
        default=0
    )

    # Filter rows where Weight_lbs > 0
    cosvi_df_final = cosvi_df_final[cosvi_df_final["Weight_lbs"] > 0]
    
    logger.info(f"cosvi_df_final after calcs: {cosvi_df_final.columns.tolist()}")

    # Select and arrange final columns
    final_columns = [
        "AssessmentNumber", "Class_of_Supply", "Region", "Location_ID", "Location_Name", "Region_MEF_Lead",
        "COS_Type", "POS", "UI", "Qty", "Pallets", "CUFT", "TEUS", "Weight_lbs", "Total_Cost"
    ]

    cosvi_df_final = cosvi_df_final[final_columns]
    
    logger.info(f"cosvi_df_final final: {cosvi_df_final.columns.tolist()}")

    # Convert all column names to uppercase
    cosvi_df_final.columns = cosvi_df_final.columns.str.upper()

    # Convert DataFrame to CSV and upload to S3
    csv_buffer = StringIO()
    cosvi_df_final.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=target_bucket, Key=output_file_path, Body=csv_buffer.getvalue())
    logger.info("Class VI Embark CSV file has been uploaded to S3.")

    # Return the S3 file path
    return {
        'statusCode': 200,
        'body': 'Processing complete',
        's3_file_path': f's3://{target_bucket}/{output_file_path}'
    }


def lambda_handler(event, context):
    """Lambda function entry point."""
    logger.info("COS VI Embark transformation started.")
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
        result = cosvi_embark(s3_client, source_bucket, s3_key, assessment_id, target_bucket)
        logger.info("COS VI Embark File Processed.")
        return result

    except Exception as e:
        logger.error(f"Error processing COS VI Embark: {str(e)}")
        return {
            'statusCode': 500,
            'body': f"Error processing COS VI Embark: {str(e)}"
        }
