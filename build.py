import os
import sys
import subprocess
from boto3.session import Session
from validate import ValidateNotebook

# Get the list of Jupyter Notebook files from the command-line argument
ipynb_files = sys.argv[1].split()
JobInfoTableName = sys.argv[2]
S3LogBucketName = sys.argv[3]
SageMakerRole = sys.argv[4]
RevisionId = sys.argv[5]

db_table = Session().resource("dynamodb").Table(JobInfoTableName)

def create_job():
    pass

if len(ipynb_files) == 0:
    print("No Notebooks to validate")
    return
for notebook in ipynb_files:
    print(f"Validating {notebook}...")
    is_valid = ValidateNotebook(bucket_name=S3LogBucketName, role_arn=SageMakerRole, local_filepath=notebook, revision_id=RevisionId)

if len(ipynb_files) != 0:
    print("Notebooks validated successfully")
