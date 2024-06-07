import sys
from boto3.session import Session
from validate import ValidateNotebook
import yaml
from datetime import datetime
import concurrent.futures

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
            "time": datetime.now().strftime("%m/%d/%Y %H:%M:%S"),
        }
    )

def create_parallel_job(notebook):
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

def create_job():
    if not ipynb_files:
        print("No Notebooks to validate")
    else:
        executable_notebooks = list()
        for notebook in ipynb_files:

            if notebook in notebook_config:
                executable_notebooks.append(notebook)
                
            else:
                print(f"{notebook} not found in order.yaml")
                continue
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(create_parallel_job, executable_notebook) for executable_notebook in executable_notebooks]

if __name__ == "__main__":
    create_job()
