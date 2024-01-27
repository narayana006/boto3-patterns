"""Error handling: catch the specific error, let everything else propagate.

Service errors arrive as ``botocore.exceptions.ClientError`` with a code in
``exc.response["Error"]["Code"]``. Problems that happen before a response
exists (no credentials, DNS failure, timeouts, invalid parameters) are
``BotoCoreError`` subclasses instead and have no ``response``.

Run the demo (in-memory fake, no AWS account needed):

    python patterns/errors.py
"""

from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError

# The codes botocore's "standard" retry mode treats as throttling.
THROTTLE_CODES = frozenset({
    "Throttling", "ThrottlingException", "ThrottledException", "RequestThrottledException",
    "TooManyRequestsException", "ProvisionedThroughputExceededException",
    "TransactionInProgressException", "RequestLimitExceeded", "BandwidthLimitExceeded",
    "LimitExceededException", "RequestThrottled", "SlowDown", "PriorRequestNotComplete",
    "EC2ThrottledException",
})
ACCESS_CODES = frozenset({
    "AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "ExpiredToken",
    "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException",
})


def error_code(exc: BaseException) -> str | None:
    """The service error code, or None when ``exc`` is not a service error."""
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code")
    return None


def http_status(exc: BaseException) -> int | None:
    if isinstance(exc, ClientError):
        return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return None


def classify(exc: BaseException) -> str:
    """Sort an exception into ``throttled``, ``access``, ``not_found``, ``client``, ``server``,
    ``local`` (it never reached the service) or ``other`` (not an AWS error at all)."""
    if isinstance(exc, BotoCoreError):
        return "local"
    code = error_code(exc)
    if code is None:
        return "other"
    if code in THROTTLE_CODES:
        return "throttled"
    if code in ACCESS_CODES:
        return "access"
    status = http_status(exc) or 0
    if status == 404:
        return "not_found"
    return "server" if status >= 500 else "client"


def get_object_or_none(s3, bucket: str, key: str) -> bytes | None:
    """Return the object's bytes, or None when the key does not exist.

    ``s3.exceptions.NoSuchKey`` is a modelled exception: a ClientError
    subclass generated per client. Catching it is more precise than catching
    ClientError and comparing strings, and anything else (access denied, a
    missing bucket) still propagates.
    """
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None


def object_exists(s3, bucket: str, key: str) -> bool:
    """Existence check with HeadObject.

    A HEAD response has no body, so the error code is the bare HTTP status
    ``"404"`` rather than ``"NoSuchKey"``. Also note that S3 answers 403
    instead of 404 for a missing key when the caller lacks ``s3:ListBucket``
    on the bucket; that case is re-raised here, not reported as "missing".
    """
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if error_code(exc) in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def ensure_bucket(s3, bucket: str) -> bool:
    """Create the bucket if needed. Returns True when it was created by this call.

    ``us-east-1`` must not be passed as a ``LocationConstraint``; every other
    region must be. ``BucketAlreadyOwnedByYou`` means the work is already
    done. ``BucketAlreadyExists`` (someone else owns the name) is a real
    failure and propagates.
    """
    region = s3.meta.region_name
    kwargs = {"Bucket": bucket}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**kwargs)
    except ClientError as exc:
        if error_code(exc) == "BucketAlreadyOwnedByYou":
            return False
        raise
    return True


def put_item_if_absent(dynamodb, table: str, item: dict, key_attribute: str) -> bool:
    """Insert ``item`` unless an item with the same key exists. Returns True when written.

    The condition makes the write safe to repeat, and the conditional-check
    failure is an expected outcome, not an error.
    """
    try:
        dynamodb.put_item(
            TableName=table,
            Item=item,
            ConditionExpression="attribute_not_exists(#k)",
            ExpressionAttributeNames={"#k": key_attribute},
        )
    except dynamodb.exceptions.ConditionalCheckFailedException:
        return False
    return True


def demo() -> None:
    import os

    import boto3
    from botocore.exceptions import ParamValidationError
    from moto import mock_aws

    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="eu-west-1",
                      AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_CONFIG_FILE=os.devnull)
    os.environ.pop("AWS_PROFILE", None)
    with mock_aws():
        s3 = boto3.client("s3")
        print("ensure_bucket, first call: ", ensure_bucket(s3, "demo-bucket"))
        print("ensure_bucket, second call:", ensure_bucket(s3, "demo-bucket"))
        s3.put_object(Bucket="demo-bucket", Key="present.txt", Body=b"hello")
        print("get_object_or_none(present.txt):", get_object_or_none(s3, "demo-bucket", "present.txt"))
        print("get_object_or_none(missing.txt):", get_object_or_none(s3, "demo-bucket", "missing.txt"))
        print("object_exists(missing.txt):", object_exists(s3, "demo-bucket", "missing.txt"))
        for label, call in [
            ("get_object on a missing bucket", lambda: s3.get_object(Bucket="no-such-bucket-demo", Key="x")),
            ("head_object on a missing key", lambda: s3.head_object(Bucket="demo-bucket", Key="missing.txt")),
            ("get_object without a Key", lambda: s3.get_object(Bucket="demo-bucket")),
        ]:
            try:
                call()
            except (ClientError, ParamValidationError) as exc:
                print(f"{label}: {type(exc).__name__} code={error_code(exc)} "
                      f"status={http_status(exc)} -> {classify(exc)}")

        dynamodb = boto3.client("dynamodb")
        dynamodb.create_table(
            TableName="orders", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        )
        item = {"id": {"S": "order-1"}, "status": {"S": "new"}}
        print("put_item_if_absent, first call: ", put_item_if_absent(dynamodb, "orders", item, "id"))
        print("put_item_if_absent, second call:", put_item_if_absent(dynamodb, "orders", item, "id"))


if __name__ == "__main__":
    demo()
