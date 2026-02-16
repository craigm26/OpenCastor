"""
RCAN Spec Validator.
Finds all *.rcan.yaml files and checks them against the RCAN JSON Schema.
"""

import os
import sys
import json
import argparse

import yaml
from jsonschema import validate, ValidationError


def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_schema(path):
    with open(path, "r") as f:
        if path.endswith(".yaml") or path.endswith(".yml"):
            return yaml.safe_load(f)
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Validate RCAN configurations.")
    parser.add_argument("--schema", required=True, help="Path to the RCAN schema file")
    parser.add_argument("--dir", required=True, help="Root directory to scan for configs")
    args = parser.parse_args()

    # Load the Master Schema
    try:
        schema = load_schema(args.schema)
        print(f"Loaded RCAN Schema from {args.schema}")
    except FileNotFoundError:
        print(f"Error: Schema file not found at {args.schema}")
        sys.exit(1)

    # Find all .rcan.yaml files
    files_to_check = []
    for root, _, files in os.walk(args.dir):
        # Skip hidden directories and rcan-spec checkout
        if any(part.startswith(".") for part in root.split(os.sep)):
            continue
        if "rcan-spec" in root:
            continue
        for file in files:
            if file.endswith(".rcan.yaml") or file.endswith(".rcan.yml"):
                files_to_check.append(os.path.join(root, file))

    if not files_to_check:
        print("No .rcan.yaml files found to validate.")
        sys.exit(0)

    # Validate each file
    failure = False
    print(f"Found {len(files_to_check)} config files. Validating...")

    for file_path in files_to_check:
        try:
            data = load_yaml(file_path)
            validate(instance=data, schema=schema)
            print(f"  [PASS] {file_path}")
        except ValidationError as e:
            print(f"  [FAIL] {file_path}")
            print(f"    >>> {e.message}")
            failure = True
        except Exception as e:
            print(f"  [ERR ] {file_path}: {e}")
            failure = True

    if failure:
        print("\nValidation failed. Please fix the errors above.")
        sys.exit(1)
    else:
        print("\nAll configurations are RCAN compliant!")
        sys.exit(0)


if __name__ == "__main__":
    main()
