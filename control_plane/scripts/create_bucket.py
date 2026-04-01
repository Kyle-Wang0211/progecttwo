from __future__ import annotations

import os

import boto3


def main() -> None:
    endpoint = os.environ["CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL"]
    access = os.environ["CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID"]
    secret = os.environ["CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY"]
    bucket = os.environ["CONTROL_PLANE_OBJECT_STORAGE_BUCKET"]
    region = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_REGION") or None

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
    )

    try:
        client.head_bucket(Bucket=bucket)
        print("bucket_exists")
    except Exception:
        client.create_bucket(Bucket=bucket)
        print("bucket_created")


if __name__ == "__main__":
    main()
