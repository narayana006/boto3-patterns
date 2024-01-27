"""S3 multipart uploads: the managed way, the manual way, and cleaning up leftovers.

* ``upload_file_multipart``: let boto3's transfer manager split, parallelise
  and retry the parts. Use this unless you have a reason not to.
* ``upload_parts``: the explicit API calls, for data that arrives as a
  stream of chunks. The important part is the ``abort`` on failure: the
  parts of an unfinished upload stay in the bucket, and are billed, until
  the upload is completed or aborted.
* ``find_incomplete_uploads`` / ``abort_incomplete_uploads``: list and
  remove uploads that were never finished. Aborting is a dry run unless
  ``apply=True``.

Run the demo (in-memory fake, no AWS account needed):

    python patterns/s3_multipart.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from boto3.s3.transfer import TransferConfig

MIB = 1024 * 1024
MIN_PART_SIZE = 5 * MIB   # S3 minimum for every part except the last
MAX_PARTS = 10_000        # S3 maximum number of parts per upload


def upload_file_multipart(s3, path: str, bucket: str, key: str, *, part_size: int = 16 * MIB,
                          max_concurrency: int = 8, extra_args: dict | None = None) -> None:
    """Upload a local file; files larger than ``part_size`` go up as parallel parts.

    The transfer manager aborts the multipart upload itself if a part fails.
    """
    config = TransferConfig(
        multipart_threshold=part_size,
        multipart_chunksize=part_size,
        max_concurrency=max_concurrency,
    )
    s3.upload_file(path, bucket, key, Config=config, ExtraArgs=extra_args)


def upload_parts(s3, bucket: str, key: str, chunks: Iterable[bytes]) -> int:
    """Upload an iterable of byte chunks as one object. Returns the number of parts.

    Every chunk except the last must be at least 5 MiB. On any error the
    upload is aborted so no orphaned parts are left behind, then the error
    is re-raised.
    """
    upload_id = s3.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
    parts = []
    try:
        for number, chunk in enumerate(chunks, start=1):
            if number > MAX_PARTS:
                raise ValueError(f"more than {MAX_PARTS} parts; use larger chunks")
            response = s3.upload_part(Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=chunk)
            parts.append({"PartNumber": number, "ETag": response["ETag"]})
        if not parts:
            raise ValueError("no data to upload")
        s3.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
        )
    except BaseException:
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise
    return len(parts)


def find_incomplete_uploads(s3, bucket: str, *, older_than: timedelta = timedelta(days=1),
                            now: datetime | None = None) -> list[dict]:
    """List multipart uploads started more than ``older_than`` ago and never finished."""
    cutoff = (now or datetime.now(timezone.utc)) - older_than
    stale = []
    for page in s3.get_paginator("list_multipart_uploads").paginate(Bucket=bucket):
        for upload in page.get("Uploads", []):
            if upload["Initiated"] <= cutoff:
                stale.append({"Key": upload["Key"], "UploadId": upload["UploadId"],
                              "Initiated": upload["Initiated"]})
    return stale


def abort_incomplete_uploads(s3, bucket: str, *, older_than: timedelta = timedelta(days=1),
                             apply: bool = False, now: datetime | None = None) -> list[dict]:
    """Abort stale uploads. Returns what was (or, without ``apply``, would be) aborted.

    For a permanent fix, add a bucket lifecycle rule with
    ``AbortIncompleteMultipartUpload`` so S3 does this on its own.
    """
    stale = find_incomplete_uploads(s3, bucket, older_than=older_than, now=now)
    if apply:
        for upload in stale:
            s3.abort_multipart_upload(Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"])
    return stale


def demo() -> None:
    import os
    import tempfile

    import boto3
    from moto import mock_aws

    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="us-east-1",
                      AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_CONFIG_FILE=os.devnull)
    os.environ.pop("AWS_PROFILE", None)
    with mock_aws(), tempfile.TemporaryDirectory() as workdir:
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="demo-bucket")

        path = os.path.join(workdir, "backup.bin")
        with open(path, "wb") as handle:
            handle.write(b"\0" * (12 * MIB))
        upload_file_multipart(s3, path, "demo-bucket", "managed/backup.bin", part_size=5 * MIB)
        head = s3.head_object(Bucket="demo-bucket", Key="managed/backup.bin")
        print(f"upload_file_multipart: {head['ContentLength']} bytes, ETag {head['ETag']}")

        chunks = [b"a" * MIN_PART_SIZE, b"b" * MIN_PART_SIZE, b"c" * 1024]
        count = upload_parts(s3, "demo-bucket", "manual/stream.bin", chunks)
        head = s3.head_object(Bucket="demo-bucket", Key="manual/stream.bin")
        print(f"upload_parts: {count} parts, {head['ContentLength']} bytes, ETag {head['ETag']}")

        def failing_chunks():
            yield b"a" * MIN_PART_SIZE
            raise OSError("source stream closed")

        try:
            upload_parts(s3, "demo-bucket", "manual/broken.bin", failing_chunks())
        except OSError as exc:
            left = s3.list_multipart_uploads(Bucket="demo-bucket").get("Uploads", [])
            print(f"upload_parts with a failing source: {exc}; uploads left behind: {len(left)}")

        s3.create_multipart_upload(Bucket="demo-bucket", Key="abandoned/video.mp4")
        would = abort_incomplete_uploads(s3, "demo-bucket", older_than=timedelta(0))
        print(f"abort_incomplete_uploads dry run: would abort {[u['Key'] for u in would]}")
        abort_incomplete_uploads(s3, "demo-bucket", older_than=timedelta(0), apply=True)
        left = s3.list_multipart_uploads(Bucket="demo-bucket").get("Uploads", [])
        print(f"abort_incomplete_uploads apply=True: uploads left: {len(left)}")


if __name__ == "__main__":
    demo()
