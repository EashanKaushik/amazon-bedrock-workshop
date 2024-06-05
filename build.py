import os
import sys
import subprocess
from boto3.session import Session

# Get the list of Jupyter Notebook files from the command-line argument
ipynb_files = sys.argv[1].split()
JobInfoTableName = sys.argv[2]

db_table = Session().resource("dynamodb").Table(JobInfoTableName)

if len(ipynb_files) == 0:
    print("No Notebooks to validate")

for notebook in ipynb_files:
    print(f"Validating {notebook}...")
    output_file = f"/tmp/{os.path.basename(notebook)}.out"

    try:
        subprocess.run(
            [
                "nbconvert",
                "--to",
                "notebook",
                "--execute",
                "--allow-errors",
                notebook,
                "--output",
                output_file,
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Errors found in {notebook}. Check {output_file} for details.")
        print(e.output.decode())
        sys.exit(1)

if len(ipynb_files) != 0:
    print("Notebooks validated successfully")
