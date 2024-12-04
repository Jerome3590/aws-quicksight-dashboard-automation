# **AWS QuickSight Dashboard Automation**

This innovative project revolutionizes the way organizations handle data visualization and analysis. By leveraging the power of AWS services, this solution provides a seamless and dynamic approach to generating dashboards based on datasets uploaded to an S3 bucket.

---

## **Project Overview**

The core functionality of this project lies in its ability to automatically create and update AWS QuickSight dashboards when new CSV files are uploaded to a designated S3 bucket. This automation significantly reduces the manual effort required in dashboard creation and maintenance, enabling more efficient data analysis and decision-making.

### **Key Features and Benefits**

#### **Dynamic Dashboard Generation**
- **Automatic Updates**: Dashboards are created or updated in real-time as new data is uploaded to S3.
- **Flexibility**: Supports various data formats, with a primary focus on CSV files.

#### **Scenario Modeling and Simulation**
- **Course of Action (COA) Analysis**: Enables easy comparison of different scenarios or strategies.
- **What-If Analysis**: Facilitates exploration of potential outcomes based on varying input parameters.

#### **Scalability and Performance**
- **AWS Infrastructure**: Utilizes robust AWS services for reliable and scalable operations.
- **Efficient Data Processing**: Leverages AWS QuickSight's optimized data handling capabilities.

![IMAT Dashboard](README/dashboard1.png)

---

## **Final Architecture**

![AWS Final Architecture](README/qs_arch.png)

---

## **Workflow**

### **1. S3 Alert Trigger**

- Use AWS S3 notifications to trigger Lambda functions.
- Suffix keys (file names with extensions) are used to limit notifications to the exact files of interest, reducing unnecessary API calls.

![S3 Alerts](README/s3_trigger_file_filter.png)

[**S3 Event Triggers**](https://github.com/canallc/quicksight-dashboard-automation/tree/main/setup/s3_events)

---

### **2. Lambda Router**

- Routes messages (S3 Alerts) to AWS Step Function or AWS SQS depending on processing requirements.
- Creates and uploads a manifest file to AWS S3 for QuickSight to read.

---

### **3. SQS Processor**

- **Throttling Management**: APIs are throttled at 5 transactions per second (TPS) per user principal and 25 TPS per account. SQS helps manage these limits by controlling the rate of API calls.
- **Queue Notifications**: Receives notifications after a QuickSight manifest is created.
- **Dataset Processing**: Dataset processing happens in this stage.
- **Message Processing**: Lambda trigger #2 processes these messages one by one.

![SQS Processing](README/sqs.png)

[**SQS Setup**](https://github.com/canallc/quicksight-dashboard-automation/tree/main/setup/sqs)

---

### **4. DynamoDB Orchestration**

- **State Management**: Manages state and stores the dataset lifecycle, from creation to dashboard generation.
- **Event-Driven Processing**: Uses state changes and events in DynamoDB to trigger specialized processing by AWS Lambda.

![DynamoDB](README/dynamodb.png)

[**Lambda Functions**](https://github.com/canallc/quicksight-dashboard-automation/tree/main/setup/lambda)  
[**DynamoDB/DynamoDB Streams**](https://github.com/canallc/quicksight-dashboard-automation/tree/main/setup/dynamoDB)

---

### **Aggregated Embark Lambda Data Refresh/Data Ingest Function**

- Monitors `aggregated_embark.csv` for row count differences.
- Calls the data refresh API to update the dataset for the final dashboard once the row count is stable after successive aggregations.

![Dataset Ingest/Refresh](README/dataset_ingest.png)

---

### **Dashboard Processor**

- **Dataset Syncing**: Listens for the `DATASETS_SYNCED` event.
- **Dynamic Dashboard Creation**: Creates dashboards from the dashboard definition file.
- **Sheet and Visual Filters**: Filters dashboard sheets and visuals based on actual datasets in the assessment.

![DATASET_SYNC](README/dataset_sync.png)

---

### **5. Step Function**
*(Lambdas #5-#15)*

![State Machine Model](README/state_machine2.png)

- **Preprocessing**: Manages dataset preprocessing for aggregations and joins.
- **Aggregation and Joining**: Performs joins and aggregates all Class of Supply Embark files.
- **Sync Verification**: Checks files in AWS S3 against datasets in DynamoDB for sync status.

![State Machine Processing](README/state_machine.png)

[**Step Function Setup**](https://github.com/canallc/quicksight-dashboard-automation/tree/main/setup/step_functions)
