import boto3
import time
import base64
import sys
import uuid 


class ValidateNotebook:
    def __init__(
        self,
        bucket_name,
        role_arn,
        notebook,
        file_config,
        notebook_config,
        revision_id,
    ) -> None:
        self.bucket_name = bucket_name
        self.role_arn = role_arn
        self.file_config = file_config
        self.notebook = notebook
        self.notebook_config = notebook_config
        self.revision_id = revision_id

        self.notebook_filepath = file_config["filepath"]
        self.notebook_file_name = file_config["filename"]
        self.lifecycle_config_name = "cicd" + str(file_config["id"]) + str(uuid.uuid1().hex[:8])
        self.notebook_instance_name = "cicd" + str(file_config["id"]) + str(uuid.uuid1().hex[:8])
        self.instance_type = "ml.t3.medium"

        # Initialize boto3 clients
        self.s3_client = boto3.client("s3")
        self.sagemaker_client = boto3.client("sagemaker")

    def upload_to_s3(self, local_filepath, bucket_filename):
        try:
            self.s3_client.upload_file(
                local_filepath,
                self.bucket_name,
                f"notebooks/{self.revision_id}/{self.notebook_filepath}/{bucket_filename}",
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
        with open("error-setup.log", "w") as f:
            f.write(error_message)
        s3_key = (
            f"notebooks/{self.revision_id}/{self.notebook_filepath}/error-setup.log"
        )
        self.s3_client.upload_file("error-setup.log", self.bucket_name, s3_key)

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

    def create_lifecycle_config(
        self,
        commands_lifecycle_config_setup,
        commands_lifecycle_config_clean_up,
        commands_lifecycle_config_errors,
    ):

        if len(commands_lifecycle_config_setup) != 0:
            setup_commands = "\n".join(commands_lifecycle_config_setup)
        else:
            setup_commands = ""

        if len(commands_lifecycle_config_clean_up) != 0:
            clean_up_commands = "\n".join(commands_lifecycle_config_clean_up)
        else:
            clean_up_commands = ""

        if len(commands_lifecycle_config_errors) != 0:
            error_commands = "\n".join(commands_lifecycle_config_errors)
        else:
            error_commands = ""

        lifecycle_script = f"""#!/bin/bash
set -e

BUCKET_NAME={self.bucket_name}
REVISION_ID={self.revision_id}
S3_NOTEBOOK_PATH=notebooks/$REVISION_ID/{self.notebook_filepath}/{self.notebook_file_name}
DEST_PATH=/home/ec2-user/SageMaker
NOTEBOOK_FULL_PATH=$DEST_PATH/{self.notebook_file_name}
OUTPUT_NOTEBOOK=$DEST_PATH/output-{self.notebook_file_name}
sudo -u ec2-user -i <<EOF
aws s3 cp s3://$BUCKET_NAME/$S3_NOTEBOOK_PATH $DEST_PATH/
{setup_commands}
jupyter nbconvert --to notebook --execute --allow-errors --output $OUTPUT_NOTEBOOK $NOTEBOOK_FULL_PATH --ExecutePreprocessor.enabled=True --ExecutePreprocessor.kernel_name=conda_python3
{clean_up_commands}
if grep -q '"ename":' $OUTPUT_NOTEBOOK; then
    echo "Error found in notebook execution" > $DEST_PATH/{self.notebook_file_name}-error-exec.log
    aws s3 cp $DEST_PATH/{self.notebook_file_name}-error-exec.log s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.notebook_filepath}/{self.notebook_file_name}-error-exec.log
    aws s3 cp $OUTPUT_NOTEBOOK s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.notebook_filepath}/output-{self.notebook_file_name}
else
    echo "No Error found in notebook execution"
    aws s3 cp $OUTPUT_NOTEBOOK s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.notebook_filepath}/output-{self.notebook_file_name}
fi
{error_commands}
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
                self.create_error_log(
                    f"Instance created but Lifecycle Config failed to execute."
                )
                self.delete_lifecycle_config()
                self.delete_notebook_instance()
                return False
            time.sleep(30)

    def check_for_errors(self, error_log_file):
        continuation_token = None
        response = True
        while True:
            if continuation_token:
                response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=f"notebooks/{self.revision_id}/{self.notebook_filepath}", ContinuationToken=continuation_token)
            else:
                response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=f"notebooks/{self.revision_id}/{self.notebook_filepath}")

            for obj in response.get('Contents', []):
                key = obj['Key']
                if 'error' in key:
                    response = False
                    print(f"Found file with 'error' in the name: {key}")

            if 'NextContinuationToken' in response:
                continuation_token = response['NextContinuationToken']
            else:
                break
        
        return response
        # try:
        #     self.s3_client.head_object(Bucket=self.bucket_name, Key=error_log_file)
        #     print(f"Error log found: {error_log_file}")
        #     return True
        # except self.s3_client.exceptions.ClientError as e:
        #     if e.response["Error"]["Code"] == "404":
        #         print("No errors found in notebook execution.")
        #         return False
        #     else:
        #         raise

    def validate_notebook(self):
        commands_lifecycle_config_setup = list()
        commands_lifecycle_config_clean_up = list()
        commands_lifecycle_config_errors = list()

        if "commands_lifecycle_config" in self.file_config:
            commands_lifecycle_config_setup.extend(
                self.file_config["commands_lifecycle_config"]
            )

        if "copy_artifacts_to_s3" in self.file_config:
            for copy_artifacats in self.file_config["copy_artifacts_to_s3"]:
                self.upload_to_s3(
                    local_filepath=copy_artifacats["local_filepath"],
                    bucket_filename=copy_artifacats["bucket_filename"],
                )
                commands_lifecycle_config_setup.append(
                    f"aws s3 cp s3://{self.bucket_name}/notebooks/{self.revision_id}/{self.notebook_filepath}/{copy_artifacats['bucket_filename']} $DEST_PATH/"
                )

        # if "notebook_dependency" in self.file_config:
        #     pass
        #     TODO: 1. upload artifacts, upload dependent notebook
        #           2. add commands to lifecycle -> commands_lifecycle_config, command to run dependent notebook

        # TODO: check if notebook clean
        # TODO: if notebook_dependency check for notebook_clean

        if "notebook_clean" in self.file_config:
            for notebook_clean in self.file_config["notebook_clean"]:
                self.upload_to_s3(
                    local_filepath=notebook_clean["local_filepath"],
                    bucket_filename=notebook_clean["bucket_filename"],
                )
                commands_lifecycle_config_setup.append(
                    f"aws s3 cp s3://{self.bucket_name}/notebooks/{self.revision_id}/{self.notebook_filepath}/{notebook_clean['bucket_filename']} $DEST_PATH/"
                )
                commands_lifecycle_config_clean_up.append(
                    f"jupyter nbconvert --to notebook --execute --allow-errors --output $DEST_PATH/output-{notebook_clean['bucket_filename']} $DEST_PATH/{notebook_clean['bucket_filename']} --ExecutePreprocessor.enabled=True --ExecutePreprocessor.kernel_name=conda_python3"
                )
                commands_lifecycle_config_errors.append(
                    f"""                                           
if grep -q '"ename":' $DEST_PATH/output-{notebook_clean["bucket_filename"]}; then
    echo "Error found in notebook execution" > $DEST_PATH/{notebook_clean["bucket_filename"]}-error-exec.log
    aws s3 cp $DEST_PATH/{notebook_clean["bucket_filename"]}-error-exec.log s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.notebook_filepath}/{notebook_clean["bucket_filename"]}-error-exec.log
    aws s3 cp $DEST_PATH/output-{notebook_clean["bucket_filename"]} s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.notebook_filepath}/output-{notebook_clean["bucket_filename"]}
else
    echo "No Error found in notebook execution"
    aws s3 cp $OUTPUT_NOTEBOOK s3://$BUCKET_NAME/notebooks/{self.revision_id}/{self.notebook_filepath}/output-{self.notebook_file_name}
fi
"""
                )

        if (
            self.upload_to_s3(
                local_filepath=self.notebook, bucket_filename=self.notebook_file_name
            )
            and self.create_lifecycle_config(
                commands_lifecycle_config_setup=commands_lifecycle_config_setup,
                commands_lifecycle_config_clean_up=commands_lifecycle_config_clean_up,
                commands_lifecycle_config_errors=commands_lifecycle_config_errors,
            )
            and self.create_notebook_instance()
            and self.wait_for_instance()
        ):
            print("Setup completed.")
        else:
            print("Setup failed.")
            result = False

        if self.check_for_errors(
            f"notebooks/{self.revision_id}/{self.notebook_filepath}/error.log"
        ):
            print("Notebook execution failed. Check the error log in S3 for details.")
            result = False
        else:
            print("Notebook executed successfully.")
            result = True

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

            waiter = self.sagemaker_client.get_waiter("notebook_instance_deleted")
            waiter.wait(NotebookInstanceName=self.notebook_instance_name)
            print(f"Notebook instance {self.notebook_instance_name} deleted.")
        except Exception as e:
            print(f"Notebook instance {self.notebook_instance_name} error {e}")


if __name__ == "__main__":
    validate = ValidateNotebook(
        bucket_name=sys.argv[1],
        role_arn=sys.argv[2],
        notebook=sys.argv[3],
        file_config=sys.argv[4],
        revision_id=sys.argv[5],
    )

    print(validate.validate_notebook())
