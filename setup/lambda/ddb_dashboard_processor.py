import os
import json
import logging
import shutil
from datetime import datetime
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
from tenacity import retry, stop_after_attempt, wait_exponential

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
s3_client = boto3.client('s3')
dynamodb_client = boto3.client('dynamodb')
dynamodb_resource = boto3.resource('dynamodb', region_name='us-east-1')
quicksight_client = boto3.client('quicksight')

# Configuration
dynamodb_table = os.getenv('DYNAMODB_TABLE', 'imat-dashboard-datasets')
s3_bucket = os.getenv('S3_BUCKET', 'imat-dashboard-definitions')
aws_account_id = os.getenv('AWS_ACCOUNT_ID', 'xxxxxxxxxxxx')
region = os.getenv('AWS_REGION', 'us-east-1')
owner_arn = os.getenv('OWNER_ARN', 'arn:aws:quicksight:us-east-1:xxxxxxxxxxxx:user/default/quicksight')


source_path_templates = '/var/task/templates/'
destination_path_templates = '/tmp/templates/'
dashboard_definition_path = '/tmp/templates/dashboard_definition.json'
dashboard_template_path = '/tmp/templates/dashboard_template.json'


def copy_files_to_tmp(source_path, destination_path):
    os.makedirs(destination_path, exist_ok=True)
    for filename in os.listdir(source_path):
        shutil.copy(os.path.join(source_path, filename), os.path.join(destination_path, filename))


# Copy files from templates to /tmp
copy_files_to_tmp(source_path_templates, destination_path_templates)


def read_json_file(file_path):
    try:
        with open(file_path, 'r') as file:
            data = json.load(file)
            logger.info(f"Successfully read JSON from {file_path}")
            return data
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from file: {file_path}")
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {str(e)}")
    return None


def read_txt_file(file_path):
    try:
        with open(file_path, 'r') as file:
            data = file.read()
            logger.info(f"Successfully read text from {file_path}")
            return data
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {str(e)}")
    return None


def update_dashboard_base_definition(dashboard_definition_path, assessment_id):
    try:
        # Read the base definition from the text file
        def_base = read_txt_file(dashboard_definition_path)
        if def_base is None:
            logger.error(f"Failed to read dashboard base definition from {dashboard_definition_path}")
            return None

        # Perform string replacement
        logger.info(f"Updating dashboard definition for assessment_id: {assessment_id}")
        updated_def = def_base.replace('template', f'{assessment_id}')

        # Try to parse the updated string as JSON
        updated_def_json = json.loads(updated_def)
        logger.info(f"Successfully updated and parsed dashboard definition for assessment_id: {assessment_id}")
        return updated_def_json

    except json.JSONDecodeError:
        logger.error(f"Error decoding updated dashboard definition for assessment_id: {assessment_id}")
    except Exception as e:
        logger.error(f"Error updating dashboard base definition for assessment_id {assessment_id}: {str(e)}")

    return None


def query_dynamodb_for_datasets(dynamodb_resource, dynamodb_table, assessment_id):
    try:

        # Query DynamoDB to check if the dataset exists
        table = dynamodb_resource.Table(dynamodb_table)
        response = table.query(
            KeyConditionExpression=Key('assessment_id').eq(assessment_id),
            FilterExpression=Attr('Dataset_Created').exists()
        )

        # Extract dataset_ids from the response
        datasets = [item['dataset_id'] for item in response.get('Items', [])]

        # Log the available datasets
        logger.info(f"Available datasets for assessment_id {assessment_id}: {datasets}")

        return datasets
    except Exception as e:
        logger.error(f"Error querying DynamoDB: {e}")
        raise


def convert_datasetID_to_identifier(available_datasets):
    dataset_identifiers = []
    for dataset in available_datasets:
        assessment_id = dataset.split('-dataset-')[1]
        identifier = dataset.replace('-', '_')
        dataset_identifiers.append(identifier)
    return dataset_identifiers


def convert_datasetID_to_placeholder(available_datasets, assessment_id):
    """
    Convert dataset ARNs or IDs to their respective placeholders by replacing the assessment_id with 'template'.
    """
    dataset_placeholders = []
    for dataset in available_datasets:
        try:
            # Find the part that includes the assessment_id and replace it with 'template'
            # assuming the dataset string structure contains '-dataset-{assessment_id}'
            if f"-dataset-{assessment_id}" in dataset:
                placeholder = dataset.replace(f'-dataset-{assessment_id}', '-dataset-template')
                placeholder = placeholder.replace('-', '_')  # Ensure placeholders use underscores instead of hyphens
                dataset_placeholders.append(placeholder)
            else:
                logger.warning(f"Dataset does not contain expected structure for assessment_id replacement: {dataset}")
        except Exception as e:
            logger.warning(f"Failed to convert dataset ID to placeholder for dataset: {dataset}. Error: {e}")
            continue
    return dataset_placeholders


def update_source_template(dashboard_template_path, assessment_id):
    try:
        # Read the base template definition from the file
        template_base = read_txt_file(dashboard_template_path)
        if template_base is None:
            logger.error(f"Failed to read dashboard base template from {dashboard_template_path}")
            return None

        # Replace placeholder with the assessment_id
        logger.info(f"Updating dashboard template for assessment_id: {assessment_id}")
        updated_template = template_base.replace('{assessment_id}', f'{assessment_id}')

        # Log to ensure the assessment_id replacement was successful
        if '{assessment_id}' not in updated_template:
            logger.info(f"Successfully updated assessment_id in the template for assessment_id: {assessment_id}")
        else:
            logger.warning(f"Failed to update assessment_id in the template for assessment_id: {assessment_id}")

        # Parse the updated template string into JSON
        logger.info(f"Parsing the updated template JSON for assessment_id: {assessment_id}.")
        updated_source_template_json = json.loads(updated_template)

        return updated_source_template_json

    except json.JSONDecodeError:
        logger.error("Error decoding the updated template JSON.")
    except Exception as e:
        logger.error(f"Error updating 'SourceTemplate' in the template JSON: {str(e)}")

    return None


def get_dynamic_mappings(assessment_id):
    mappings = {
        f"AssessmentTable-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_fab31ce4-3509-41cf-9eb7-58cc4ee5da03",
             "Name": "Summary",
             "FilterGroupId": None}
        ],
        f"AssessmentParameterDataMapTable-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_fab31ce4-3509-41cf-9eb7-58cc4ee5da03",
             "Name": "Summary",
             "FilterGroupId": None}
        ],
        f"Locations-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_fab31ce4-3509-41cf-9eb7-58cc4ee5da03",
             "Name": "Summary",
             "FilterGroupId": None}
        ],
        f"POS-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_fab31ce4-3509-41cf-9eb7-58cc4ee5da03",
             "Name": "Summary",
             "FilterGroupId": None}
        ],
        f"aggregated-embark-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_e64adb51-5f19-4f4e-83e0-09624c298b87",
             "Name": "Embark",
             "FilterGroupId": None}
        ],
        f"ForceFlow-joined-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_9f580198-fa0c-4dd5-975a-20fd5d1015e3",
             "Name": "Force Flow",
             "FilterGroupId": "89c9153a-994b-4b7d-b21e-ddeb72130861"}
        ],
        f"RD-I-POS-Location-joined-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_d5e7ff9e-071b-4d3b-a55a-97e9182cb2a1",
             "Name": "CL I RD",
             "FilterGroupId": None}
        ],
        f"RD-I-POS-Pallet-Requirement-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_d5e7ff9e-071b-4d3b-a55a-97e9182cb2a1",
             "Name": "CL I RD",
             "FilterGroupId": None}
        ],
        f"RD-I-Ration-Costs-joined-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_d5e7ff9e-071b-4d3b-a55a-97e9182cb2a1",
             "Name": "CL I RD",
             "FilterGroupId": None}
        ],
        f"cosi-embark-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_04766379-14f8-4d15-b0b1-65ac277d9ff5",
             "Name": "CL I RD Embark",
             "FilterGroupId": None}
        ],
        f"RD-IW-PaxLocationAll-joined-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_7736e013-62eb-4b6c-8727-831eb6649b14",
             "Name": "CL I (W) RD",
             "FilterGroupId": None}
        ],
        f"RD-II-VII-DailyTE-WithDimensions-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_00947751-ace2-46bc-97ba-72f620780a7c",
             "Name": "CL II/VII RD",
             "FilterGroupId": None}
        ],
        f"cosii-vii-embark-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_6d58bcd1-279c-4e68-a9b3-f5c45a4b92b6",
             "Name": "CL II/VII RD Embark",
             "FilterGroupId": None}
        ],
        f"cosiiip-embark-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_28d8c0d4-3cb7-4276-8fd1-5bff65d696cc",
             "Name": "CL IIIP RD",
             "FilterGroupId": None},
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_4165f722-3911-4ba3-ae64-fad37b9be62c",
             "Name": "CL IIIP RD Embark",
             "FilterGroupId": None}
        ],
        f"cosiv-embark-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_7d512cd9-604a-4fd6-91c6-001ac2900d4e",
             "Name": "CL IV RD",
             "FilterGroupId": None},
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_faf35db6-eb36-4b7d-8893-de3d2c305fd0",
             "Name": "CL IV RD Embark",
             "FilterGroupId": None}
        ],
        f"cosvi-embark-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_bfcbc8d9-3ae2-4b37-8d3f-da3347b86d45",
             "Name": "CL VI RD",
             "FilterGroupId": None},
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_f30c3e60-6dfe-4ea2-a4d5-74489ac50758",
             "Name": "CL VI RD Embark",
             "FilterGroupId": None}
        ],
        f"cosix-embark-dataset-{assessment_id}": [
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_7cedb1cf-0416-4830-bae0-ade7f6951bc6",
             "Name": "CL IX RD",
             "FilterGroupId": None},
            {"SheetId": "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_3442415d-1e80-49e2-94f3-dcf9b1a4864d",
             "Name": "CL IX RD Embark",
             "FilterGroupId": ["0f649f1f-dd14-449f-8348-7fc23438c546",
                               "d9806060-95bb-4e36-bf8a-a940b61ba7ab"]}
        ]
    }

    return mappings



def get_distinct_sheet_ids(dataset_ids, assessment_id):
    # Get dynamic mappings based on assessment_id
    mappings = get_dynamic_mappings(assessment_id)

    # Hardcoded sheet order based on the mapping function
    sheet_order = [
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_fab31ce4-3509-41cf-9eb7-58cc4ee5da03",  # Summary
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_e64adb51-5f19-4f4e-83e0-09624c298b87",  # Embark
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_9f580198-fa0c-4dd5-975a-20fd5d1015e3",  # Force Flow
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_d5e7ff9e-071b-4d3b-a55a-97e9182cb2a1",  # CL I RD
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_04766379-14f8-4d15-b0b1-65ac277d9ff5",  # CL I RD Embark
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_7736e013-62eb-4b6c-8727-831eb6649b14",  # CL I (W) RD
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_00947751-ace2-46bc-97ba-72f620780a7c",  # CL II/VII RD
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_6d58bcd1-279c-4e68-a9b3-f5c45a4b92b6",  # CL II/VII RD Embark
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_28d8c0d4-3cb7-4276-8fd1-5bff65d696cc",  # CL IIIP RD
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_4165f722-3911-4ba3-ae64-fad37b9be62c",  # CL IIIP RD Embark
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_7d512cd9-604a-4fd6-91c6-001ac2900d4e",  # CL IV RD
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_faf35db6-eb36-4b7d-8893-de3d2c305fd0",  # CL IV RD Embark
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_bfcbc8d9-3ae2-4b37-8d3f-da3347b86d45",  # CL VI RD
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_f30c3e60-6dfe-4ea2-a4d5-74489ac50758",  # CL VI RD Embark
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_7cedb1cf-0416-4830-bae0-ade7f6951bc6",  # CL IX RD
        "cb4e0c9d-3771-4997-b274-7b9e25e3b66e_3442415d-1e80-49e2-94f3-dcf9b1a4864d"   # CL IX RD Embark
    ]

    # Extract distinct sheet IDs and names for the provided dataset_ids
    result = {
        "SheetIds": set(),
        "SheetNames": set()
    }

    for dataset_id in dataset_ids:
        # Retrieve the mapping for the dataset_id
        details = mappings.get(dataset_id, [])
        for sheet in details:  # Process each sheet (list of dictionaries)
            result["SheetIds"].add(sheet["SheetId"])
            result["SheetNames"].add(sheet["Name"])

    # Convert sets back to lists and sort by the predefined order
    sorted_sheet_ids = [sheet_id for sheet_id in sheet_order if sheet_id in result["SheetIds"]]
    sorted_sheet_names = [
        sheet["Name"] for sheet in mappings.values()
        for sheet in (sheet if isinstance(sheet, list) else [sheet])
        if sheet["SheetId"] in sorted_sheet_ids
    ]

    return {
        "SheetIds": sorted_sheet_ids,
        "SheetNames": sorted_sheet_names
    }



def extract_filter_group_ids_from_mappings(dataset_ids, assessment_id):
    # Get dynamic mappings based on assessment_id
    mappings = get_dynamic_mappings(assessment_id)

    # Initialize an empty list for filter group IDs
    filter_group_ids = []

    for dataset_id in dataset_ids:
        # Retrieve the mapping for the dataset_id
        details = mappings.get(dataset_id, [])
        for sheet in details:  # Process each sheet (list of dictionaries)
            if 'FilterGroupId' in sheet and sheet['FilterGroupId']:
                if isinstance(sheet['FilterGroupId'], list):
                    filter_group_ids.extend(sheet['FilterGroupId'])  # Extend if list
                else:
                    filter_group_ids.append(sheet['FilterGroupId'])  # Append if single string

    # Return the list of filter group IDs
    return filter_group_ids


def update_dashboard_definition(assessment_id, available_datasets, dashboard_json, s3_client, s3_bucket):
    available_datasets_set = set(available_datasets)
    dataset_map = {
        dataset.replace('-', '_'): f"arn:aws:quicksight:us-east-1:xxxxxxxxxxxx:dataset/{dataset}"
        for dataset in available_datasets_set
    }

    # Update Dataset Identifier Declarations
    valid_identifiers = set(dataset_map.keys())
    updated_declarations = [
        {**declaration, 'DataSetArn': dataset_map[declaration['Identifier']]}
        for declaration in dashboard_json.get('DataSetIdentifierDeclarations', [])
        if declaration['Identifier'] in valid_identifiers
    ]
    dashboard_json['DataSetIdentifierDeclarations'] = updated_declarations

    # Update Sheets
    required_sheets = get_distinct_sheet_ids(available_datasets, assessment_id)
    sheet_id_order = required_sheets['SheetIds']  # Ordered list of SheetIds
    sheets_to_keep = set(required_sheets['SheetIds'])

    updated_sheets = [sheet for sheet in dashboard_json["Sheets"] if sheet["SheetId"] in sheets_to_keep]
    
    # Sort sheets based on the order in `sheet_id_order`
    updated_sheets.sort(key=lambda sheet: sheet_id_order.index(sheet["SheetId"]))
    
    dashboard_json["Sheets"] = updated_sheets

    # Update Calculated Fields
    updated_identifiers = {decl['Identifier'] for decl in updated_declarations}
    dashboard_json['CalculatedFields'] = [
        field for field in dashboard_json.get('CalculatedFields', [])
        if field['DataSetIdentifier'] in updated_identifiers
    ]

    # Update FilterGroups
    filter_group_ids = set(extract_filter_group_ids_from_mappings(available_datasets, assessment_id))
    dashboard_json['FilterGroups'] = [
        fg for fg in dashboard_json.get('FilterGroups', [])
        if fg['FilterGroupId'] in filter_group_ids
    ]

    # Update Column Configurations
    dashboard_json['ColumnConfigurations'] = [
        field for field in dashboard_json.get('ColumnConfigurations', [])
        if field['Column']['DataSetIdentifier'] in updated_identifiers
    ]

    # Upload to S3
    s3_key = f"{assessment_id}/dashboard_definition_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json"
    s3_client.put_object(
        Bucket=s3_bucket,
        Key=s3_key,
        Body=json.dumps(dashboard_json, indent=4),
        ContentType='application/json'
    )

    return dashboard_json



@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=10))
def create_dashboard_from_definition(quicksight_client, aws_account_id, dashboard_id, dashboard_name, dashboard_definition):
    try:
        permissions = [
            {
                "Principal": f"arn:aws:quicksight:{region}:{aws_account_id}:user/default/quicksight",
                "Actions": [
                    "quicksight:DescribeDashboard",
                    "quicksight:ListDashboardVersions",
                    "quicksight:UpdateDashboardPermissions",
                    "quicksight:QueryDashboard",
                    "quicksight:UpdateDashboard",
                    "quicksight:DeleteDashboard",
                    "quicksight:DescribeDashboardPermissions",
                    "quicksight:UpdateDashboardPublishedVersion"
                ]
            }
        ]

        # Log the dashboard creation attempt
        logger.info(f"Attempting to create custom dashboard: {dashboard_id} in account: {aws_account_id}")

        # Prepare the parameters for the create_dashboard method
        create_dashboard_params = {
            'AwsAccountId': aws_account_id,
            'DashboardId': dashboard_id,
            'Name': dashboard_name,
            'Definition': dashboard_definition,
            'Permissions': permissions,
            'VersionDescription': 'Custom dashboard with selected datasets and sheets',
            'DashboardPublishOptions': {
                'AdHocFilteringOption': {'AvailabilityStatus': 'ENABLED'},
                'ExportToCSVOption': {'AvailabilityStatus': 'ENABLED'},
                'SheetControlsOption': {'VisibilityState': 'EXPANDED'}
            }
        }

        # Call the create_dashboard method
        response = quicksight_client.create_dashboard(**create_dashboard_params)

        # Log the successful creation
        logger.info(f"Successfully created Dashboard: {dashboard_id}")
        return response

    except Exception as e:
        logger.error(f"Error creating Dashboard: {dashboard_id} in account: {aws_account_id}. Error: {e}")
        raise


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=10))
def create_dashboard_from_source_template(quicksight_client, aws_account_id, dashboard_id, dashboard_name, template_source_entity):
    try:
        permissions = [
            {
                "Principal": f"arn:aws:quicksight:{region}:{aws_account_id}:user/default/quicksight",
                "Actions": [
                    "quicksight:DescribeDashboard",
                    "quicksight:ListDashboardVersions",
                    "quicksight:UpdateDashboardPermissions",
                    "quicksight:QueryDashboard",
                    "quicksight:UpdateDashboard",
                    "quicksight:DeleteDashboard",
                    "quicksight:DescribeDashboardPermissions",
                    "quicksight:UpdateDashboardPublishedVersion"
                ]
            }
        ]

        # Log the dashboard creation attempt
        logger.info(f"Attempting to create dashboard from source template: {dashboard_id} in account: {aws_account_id}")
        if not isinstance(template_source_entity, dict):
            logger.error(f"Expected a dictionary for template_source_entity, but got {type(template_source_entity)}")
            raise TypeError("The updated source template entity is not a valid dictionary.")


        # Prepare the parameters for the create_dashboard method using SourceTemplate
        create_dashboard_params = {
            'AwsAccountId': aws_account_id,
            'DashboardId': dashboard_id,
            'Name': dashboard_name,
            'SourceEntity': template_source_entity,
            'Permissions': permissions,
            'VersionDescription': 'Dashboard created from source template',
            'DashboardPublishOptions': {
                'AdHocFilteringOption': {'AvailabilityStatus': 'ENABLED'},
                'ExportToCSVOption': {'AvailabilityStatus': 'ENABLED'},
                'SheetControlsOption': {'VisibilityState': 'EXPANDED'}
            }
        }

        # Call the create_dashboard method
        response = quicksight_client.create_dashboard(**create_dashboard_params)

        # Log the successful creation
        logger.info(f"Successfully created dashboard from template: {dashboard_id}")
        return response

    except Exception as e:
        logger.error(f"Error creating dashboard from source template: {dashboard_id} in account: {aws_account_id}. Error: {e}")
        raise


INGEST_COMPLETE_STATUS = 'INGEST_COMPLETE'


def lambda_handler(event, context):
    logger.info("Embark Aggregated Ingestion Completed. Processing Dashboard..")
    logger.info(f"Received event: {json.dumps(event)}")

    # Initialize the response variable
    response = {
        'statusCode': 200,
        'body': json.dumps({'message': 'No action taken'})
    }

    try:
        for record in event['Records']:
            if record['eventName'] in ['INSERT', 'MODIFY']:
                new_image = record['dynamodb']['NewImage']
                assessment_id = new_image['assessment_id']['S']

                # Extract and log the status if available
                file_status = new_image.get('Status', {}).get('S', 'Unknown')
                logger.info(f"File status for assessment_id {assessment_id}: {file_status}")

                if file_status == INGEST_COMPLETE_STATUS:
                    logger.info(f"Processing dashboard creation for assessment_id: {assessment_id}")

                    available_datasets = query_dynamodb_for_datasets(dynamodb_resource, dynamodb_table, assessment_id)
                    available_identifiers = convert_datasetID_to_identifier(available_datasets)

                    # Creating the dashboard using updated dashboard definition
                    try:
                        base_dashboard_json = update_dashboard_base_definition(dashboard_definition_path, assessment_id)

                        updated_dashboard_definition = update_dashboard_definition(
                            assessment_id=assessment_id,
                            available_datasets=available_datasets,
                            dashboard_json=base_dashboard_json,
                            s3_client=s3_client,
                            s3_bucket=s3_bucket
                        )

                        response = create_dashboard_from_definition(
                            quicksight_client=quicksight_client,
                            aws_account_id=aws_account_id,
                            dashboard_id=f"RD-Assessment-{assessment_id}",
                            dashboard_name=f"RD_Assessment_{assessment_id}",
                            dashboard_definition=updated_dashboard_definition
                        )

                        logger.info(f"Successfully created dashboard for assessment_id: {assessment_id} using definition.")

                    except Exception as dashboard_def_error:
                        logger.error(f"Failed to create dashboard using definition for assessment_id {assessment_id}. Error: {dashboard_def_error}")
                        raise dashboard_def_error

    except Exception as e:
        logger.error(f"Error processing record: {json.dumps(record)}: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'message': f'Error processing files: {str(e)}'})
        }

    return response

