from datetime import datetime, timedelta, timezone

import pytest
from botocore.stub import Stubber

from patterns.s3_multipart import (
    MIB, MIN_PART_SIZE, abort_incomplete_uploads, find_incomplete_uploads, upload_file_multipart, upload_parts,
)

from conftest import stub_client


def pending(s3):
    return s3.list_multipart_uploads(Bucket="test-bucket").get("Uploads", [])


def test_upload_file_multipart_splits_large_files(s3, tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(b"z" * (11 * MIB))
    upload_file_multipart(s3, str(path), "test-bucket", "big.bin", part_size=5 * MIB,
                          extra_args={"ContentType": "application/octet-stream"})
    head = s3.head_object(Bucket="test-bucket", Key="big.bin")
    assert head["ContentLength"] == 11 * MIB
    assert head["ETag"].endswith('-3"')  # a multipart ETag ends with the part count
    assert head["ContentType"] == "application/octet-stream"
    assert pending(s3) == []


def test_upload_file_multipart_small_file_is_a_single_put(s3, tmp_path):
    path = tmp_path / "small.txt"
    path.write_bytes(b"hello")
    upload_file_multipart(s3, str(path), "test-bucket", "small.txt")
    assert "-" not in s3.head_object(Bucket="test-bucket", Key="small.txt")["ETag"]


def test_upload_parts_assembles_chunks_in_order(s3):
    chunks = [b"a" * MIN_PART_SIZE, b"b" * MIN_PART_SIZE, b"tail"]
    assert upload_parts(s3, "test-bucket", "stream.bin", iter(chunks)) == 3
    body = s3.get_object(Bucket="test-bucket", Key="stream.bin")["Body"].read()
    assert body == b"".join(chunks)
    assert pending(s3) == []


def test_upload_parts_aborts_when_the_source_fails(s3):
    def chunks():
        yield b"a" * MIN_PART_SIZE
        raise OSError("disk gone")

    with pytest.raises(OSError):
        upload_parts(s3, "test-bucket", "broken.bin", chunks())
    assert pending(s3) == []
    assert "Contents" not in s3.list_objects_v2(Bucket="test-bucket")


def test_upload_parts_aborts_when_s3_rejects_the_upload(s3):
    # A non-final part under 5 MiB makes CompleteMultipartUpload fail with EntityTooSmall.
    with pytest.raises(s3.exceptions.ClientError) as excinfo:
        upload_parts(s3, "test-bucket", "tiny-parts.bin", [b"too small", b"tail"])
    assert excinfo.value.response["Error"]["Code"] == "EntityTooSmall"
    assert pending(s3) == []


def test_upload_parts_rejects_empty_input(s3):
    with pytest.raises(ValueError):
        upload_parts(s3, "test-bucket", "empty.bin", [])
    assert pending(s3) == []


def test_abort_incomplete_uploads_is_dry_run_by_default(s3):
    s3.create_multipart_upload(Bucket="test-bucket", Key="left/one")
    s3.create_multipart_upload(Bucket="test-bucket", Key="left/two")

    would = abort_incomplete_uploads(s3, "test-bucket", older_than=timedelta(0))
    assert sorted(u["Key"] for u in would) == ["left/one", "left/two"]
    assert len(pending(s3)) == 2  # nothing aborted

    done = abort_incomplete_uploads(s3, "test-bucket", older_than=timedelta(0), apply=True)
    assert len(done) == 2 and pending(s3) == []


def test_find_incomplete_uploads_only_returns_old_ones():
    # Stubbed, because moto reports a fixed start time for every upload.
    client = stub_client("s3")
    now = datetime(2024, 5, 10, 12, 0, tzinfo=timezone.utc)
    listing = {"Uploads": [
        {"Key": "old.bin", "UploadId": "u-old", "Initiated": now - timedelta(days=3)},
        {"Key": "in-progress.bin", "UploadId": "u-new", "Initiated": now - timedelta(minutes=10)},
    ]}
    with Stubber(client) as stub:
        stub.add_response("list_multipart_uploads", listing, {"Bucket": "b"})
        stale = find_incomplete_uploads(client, "b", older_than=timedelta(days=1), now=now)
        stub.add_response("list_multipart_uploads", listing, {"Bucket": "b"})
        stub.add_response("abort_multipart_upload", {}, {"Bucket": "b", "Key": "old.bin", "UploadId": "u-old"})
        abort_incomplete_uploads(client, "b", older_than=timedelta(days=1), now=now, apply=True)
        stub.assert_no_pending_responses()
    assert [u["UploadId"] for u in stale] == ["u-old"]
