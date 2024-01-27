"""Three patterns combined: list every key (pagination), HEAD them in
parallel (concurrency), and report failures by kind (errors).

Runs against moto, an in-memory fake, so no AWS account is needed:

    pip install -e ".[dev]"
    python examples/bucket_report.py

To point it at a real bucket, call ``report(boto3.Session(), "your-bucket")``
instead of ``main()``. It only reads: s3:ListBucket and s3:GetObject.
"""

import os
from collections import Counter

from patterns.concurrency import pooled_client, run_concurrently
from patterns.errors import classify
from patterns.pagination import iter_items


def report(session, bucket: str, *, max_workers: int = 16) -> None:
    s3 = pooled_client(session, "s3", max_workers)
    keys = [obj["Key"] for obj in iter_items(s3, "list_objects_v2", "Contents", Bucket=bucket)]
    types, errors = run_concurrently(
        lambda key: s3.head_object(Bucket=bucket, Key=key)["ContentType"], keys, max_workers=max_workers)
    print(f"{bucket}: {len(keys)} objects")
    for content_type, count in sorted(Counter(types.values()).items()):
        print(f"  {content_type:<26} {count}")
    for kind, count in sorted(Counter(classify(exc) for exc in errors.values()).items()):
        print(f"  failed ({kind}): {count}")


def main() -> None:
    import boto3
    from moto import mock_aws

    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="us-east-1",
                      AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_CONFIG_FILE=os.devnull)
    os.environ.pop("AWS_PROFILE", None)
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="example-reports")
        for n in range(1500):
            suffix, content_type = (("csv", "text/csv"), ("json", "application/json"), ("png", "image/png"))[n % 3]
            s3.put_object(Bucket="example-reports", Key=f"2024/{n:04d}.{suffix}", Body=b"-", ContentType=content_type)
        report(boto3.Session(), "example-reports")


if __name__ == "__main__":
    main()
