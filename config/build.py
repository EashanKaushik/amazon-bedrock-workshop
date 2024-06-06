import sys
from boto3.session import Session
from validate import ValidateNotebook
import yaml
from datetime import datetime

with open("config/notebook_config.yaml", "r") as file:
    notebook_config = yaml.safe_load(file)["OrderNotebook"]

ipynb_files = sys.argv[1].split()
JobInfoTableName = sys.argv[2]
S3LogBucketName = sys.argv[3]
SageMakerRole = sys.argv[4]
RevisionId = sys.argv[5]

db_table = Session().resource("dynamodb").Table(JobInfoTableName)


def create_job_info(is_valid, notebook, revision_id):
    db_table.put_item(
        Item={
            "notebook_path": notebook,
            "revision_id": revision_id,
            "is_valid": is_valid,
            "time": int(datetime.now().timestamp()),
        }
    )


def create_job():
    if len(ipynb_files) == 0:
        print("No Notebooks to validate")
    else:
        for notebook in ipynb_files:

            if notebook in notebook_config:
                print(f"Validating {notebook}...")
                is_valid = ValidateNotebook(
                    bucket_name=S3LogBucketName,
                    role_arn=SageMakerRole,
                    notebook=notebook,
                    file_config=notebook_config[notebook],
                    notebook_config=notebook_config,
                    revision_id=RevisionId,
                ).validate_notebook()
                    
                create_job_info(
                    is_valid=is_valid, notebook=notebook, revision_id=RevisionId
                )
            else:
                print(f"{notebook} not found in order.yaml")
                continue


if __name__ == "__main__":
    create_job()
