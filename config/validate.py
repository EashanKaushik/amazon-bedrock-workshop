import boto3
import time
import base64
import sys


class ValidateNotebook:
    def __init__(
        self,
        bucket_name,
        role_arn,
        local_filepath,
        file_config,
        notebook_config,
        revision_id,
    ) -> None:
        self.bucket_name = bucket_name
        self.role_arn = role_arn
        self.file_config = file_config
        self.local_filepath = local_filepath
        self.notebook_config = notebook_config
        self.revision_id = revision_id

        self.notebook_file_name = "run.ipynb"
        self.lifecycle_config_name = revision_id
        self.notebook_instance_name = revision_id
        self.instance_type = "ml.t3.medium"

        # Initialize boto3 clients
        self.s3_client = boto3.client("s3")
        self.sagemaker_client = boto3.client("sagemaker")

    def upload_to_s3(self, local_filepath, bucket_filename):
        try:
            self.s3_client.upload_file(
                local_filepath,
                self.bucket_name,
                f"notebooks/{self.revision_id}/{self.local_filepath.split('.')[0]}/{bucket_filename}",
            )
        except Exception as ex:
            self.create_error_log(
                f"Failed to upload file {local_filepath} to S3.\n\n{ex}"
            )
            return False
        else:
            print(f"File {local_filepath} uploaded to S3 bucket.")
            return True

    def create_error_log(self, error_message):
        with open("error.log", "w") as f:
            f.write(error_message)
        s3_key = f"notebooks/{self.revision_id}/{self.local_filepath.split('.')[0]}/error.log"
        self.s3_client.upload_file("error.log", self.bucket_name, s3_key)

    def create_notebook_instance(self):
        try:
            self.sagemaker_client.create_notebook_instance(
                NotebookInstanceName=self.notebook_instance_name,
                InstanceType=self.instance_type,
                RoleArn=self.role_arn,
                LifecycleConfigName=self.lifecycle_config_name,
            )
        except Exception as ex:
            self.create_error_log(f"Instance failed to create.\n\n{ex}")
            return False
        else:
            print(f"Notebook instance {self.notebook_instance_name} created.")
            return True

    def create_lifecycle_config(self, commands_lifecycle_config):

        if commands_lifecycle_config:
            commands = "\n".join(commands_lifecycle_config)
        else:
            commands = ""
        lifecycle_script = f"""#!/bin/bash
set -e

BUCKET_NAME={self.bucket_name}
REVISION_ID={self.revision_id}
S3_NOTEBOOK_PATH=notebooks/$REVISION_ID/{self.local_filepath.split('.')[0]}/{self.notebook_file_name}
DEST_PATH=/home/ec2-user/SageMaker
NOTEBOOK_FULL_PATH=$DEST_PATH/{self.notebook_file_name}
OUTPUT_NOTEBOOK=$DEST_PATH/output-{self.notebook_file_name}

sudo -u ec2-user -i <<EOF
aws s3 cp s3://$BUCKET_NAME/$S3_NOTEBOOK_PATH $DEST_PATH/
{commands}
jupyter nbconvert --to notebook --execute --allow-errors --output $OUTPUT_NOTEBOOK $NOTEBOOK_FULL_PATH --ExecutePreprocessor.enabled=True --ExecutePreprocessor.kernel_name=conda_python3

# Check for execution errors
if grep -q '"ename":' $OUTPUT_NOTEBOOK; then
    echo "Error found in notebook execution" > $DEST_PATH/error.log
    aws s3 cp $DEST_PATH/error.log s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.local_filepath.split('.')[0]}/error.log
    aws s3 cp $OUTPUT_NOTEBOOK s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.local_filepath.split('.')[0]}/output-{self.notebook_file_name}
else
    echo "No Error found in notebook execution"
    aws s3 cp $OUTPUT_NOTEBOOK s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.local_filepath.split('.')[0]}/output-{self.notebook_file_name}
fi
EOF
        """

        lifecycle_config = {
            "NotebookInstanceLifecycleConfigName": self.lifecycle_config_name,
            "OnStart": [
                {
                    "Content": base64.b64encode(
                        lifecycle_script.encode("utf-8")
                    ).decode("utf-8")
                }
            ],
        }
        try:
            self.sagemaker_client.create_notebook_instance_lifecycle_config(
                **lifecycle_config
            )
        except Exception as ex:
            self.create_error_log(f"Lifecycle config failed to create.\n\n{ex}")
            return False
        else:
            print(f"Lifecycle configuration {self.lifecycle_config_name} created.")
            return True

    def wait_for_instance(self):
        while True:
            try:
                response = self.sagemaker_client.describe_notebook_instance(
                    NotebookInstanceName=self.notebook_instance_name
                )
                status = response["NotebookInstanceStatus"]
                print(f"Notebook instance status: {status}")
            except Exception as ex:
                print(f"Notebook instance status error.\n\n{ex}")
                return False

            if status == "InService":
                return True
            elif status == "Failed":
                print("Instance created but Lifecycle Config failed to execute.")
                self.create_error_log(f"Instance created but Lifecycle Config failed to execute.")
                self.delete_lifecycle_config()
                self.delete_notebook_instance()
                return False
            time.sleep(30)

    def check_for_errors(self, error_log_file):
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=error_log_file)
            print(f"Error log found: {error_log_file}")
            return True
        except self.s3_client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                print("No errors found in notebook execution.")
                return False
            else:
                raise

    def validate_notebook(self):
        commands_lifecycle_config = list()
        
        if "commands_lifecycle_config" in self.file_config:
            commands_lifecycle_config.extend(
                self.file_config["commands_lifecycle_config"]
            )
        # if "copy_artifacts_to_s3" in self.file_config:
        #     for copy_artifacats in self.file_config["copy_artifacts_to_s3"]:
        #         self.upload_to_s3(
        #             local_filepath=copy_artifacats[
        #                 "local_filepath"
        #             ],
        #             bucket_filename=copy_artifacats[
        #                 "bucket_filename"
        #             ],
        #         )

        # if "notebook_dependency" in self.file_config:
        #     pass
        #     TODO: 1. upload artifacts, upload dependent notebook
        #           2. add commands to lifecycle -> commands_lifecycle_config, command to run dependent notebook

        if (
            self.upload_to_s3(self.local_filepath, self.notebook_file_name)
            and self.create_lifecycle_config(
                commands_lifecycle_config=commands_lifecycle_config
            )
            and self.create_notebook_instance()
            and self.wait_for_instance()
        ):
            print("Setup completed.")
        else:
            print("Setup failed.")
            result = False

        if self.check_for_errors(
            f"notebooks/{self.revision_id}/{self.local_filepath.split('.')[0]}/error.log"
        ):
            print("Notebook execution failed. Check the error log in S3 for details.")
            result = False
        else:
            print("Notebook executed successfully.")
            result = True
            # TODO: check if notebook clean
            # TODO: if notebook_dependency check for notebook_clean

        self.delete_lifecycle_config()
        self.delete_notebook_instance()

        return result

    def delete_lifecycle_config(self):
        try:
            self.sagemaker_client.delete_notebook_instance_lifecycle_config(
                NotebookInstanceLifecycleConfigName=self.lifecycle_config_name
            )
            print(f"Lifecycle configuration {self.lifecycle_config_name} deleted.")
        except Exception as e:
            print(f"Lifecycle configuration {self.lifecycle_config_name} error {e}")

    def delete_notebook_instance(self):
        try:
            self.sagemaker_client.stop_notebook_instance(
                NotebookInstanceName=self.notebook_instance_name
            )

            # Wait for the Notebook Instance to stop (you can add a loop to check the status)
            waiter = self.sagemaker_client.get_waiter("notebook_instance_stopped")
            waiter.wait(NotebookInstanceName=self.notebook_instance_name)
            self.sagemaker_client.delete_notebook_instance(
                NotebookInstanceName=self.notebook_instance_name
            )
            print(f"Notebook instance {self.notebook_instance_name} deleted.")
        except Exception as e:
            print(f"Notebook instance {self.notebook_instance_name} error {e}")


if __name__ == "__main__":
    validate = ValidateNotebook(
        bucket_name=sys.argv[1],
        role_arn=sys.argv[2],
        local_filepath=sys.argv[3],
        file_config=sys.argv[4],
        revision_id=sys.argv[5],
    )

    print(validate.validate_notebook())
