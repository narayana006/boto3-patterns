import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ParamValidationError
from botocore.stub import Stubber

from patterns import errors

from conftest import stub_client


def client_error(code, status=400):
    return ClientError({"Error": {"Code": code, "Message": "m"},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "Op")


@pytest.mark.parametrize("exc, expected", [
    (client_error("ThrottlingException"), "throttled"),
    (client_error("SlowDown", 503), "throttled"),
    (client_error("AccessDenied", 403), "access"),
    (client_error("ExpiredToken"), "access"),
    (client_error("NoSuchKey", 404), "not_found"),
    (client_error("ValidationException"), "client"),
    (client_error("InternalError", 500), "server"),
    (EndpointConnectionError(endpoint_url="https://example.invalid"), "local"),
    (ValueError("x"), "other"),
])
def test_classify(exc, expected):
    assert errors.classify(exc) == expected


def test_error_code_and_status_for_non_service_errors():
    assert errors.error_code(ValueError("x")) is None
    assert errors.http_status(ValueError("x")) is None


def test_get_object_or_none(s3):
    s3.put_object(Bucket="test-bucket", Key="a", Body=b"data")
    assert errors.get_object_or_none(s3, "test-bucket", "a") == b"data"
    assert errors.get_object_or_none(s3, "test-bucket", "missing") is None
    with pytest.raises(ClientError) as excinfo:  # a missing bucket is not swallowed
        errors.get_object_or_none(s3, "no-such-bucket-here", "a")
    assert errors.error_code(excinfo.value) == "NoSuchBucket"


def test_object_exists(s3):
    s3.put_object(Bucket="test-bucket", Key="a", Body=b"")
    assert errors.object_exists(s3, "test-bucket", "a") is True
    assert errors.object_exists(s3, "test-bucket", "missing") is False


def test_object_exists_reraises_access_denied():
    s3 = stub_client("s3")
    with Stubber(s3) as stub:
        stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
        with pytest.raises(ClientError):
            errors.object_exists(s3, "bucket", "key")


def test_ensure_bucket_is_idempotent_outside_us_east_1(aws):
    s3 = boto3.client("s3", region_name="eu-west-1")
    assert errors.ensure_bucket(s3, "fresh-bucket") is True
    assert errors.ensure_bucket(s3, "fresh-bucket") is False
    assert s3.get_bucket_location(Bucket="fresh-bucket")["LocationConstraint"] == "eu-west-1"


def test_ensure_bucket_sends_no_location_constraint_in_us_east_1():
    s3 = stub_client("s3")
    with Stubber(s3) as stub:
        stub.add_response("create_bucket", {}, {"Bucket": "b"})  # exact params: no CreateBucketConfiguration
        assert errors.ensure_bucket(s3, "b") is True


def test_ensure_bucket_propagates_name_taken_by_someone_else():
    s3 = stub_client("s3")
    with Stubber(s3) as stub:
        stub.add_client_error("create_bucket", service_error_code="BucketAlreadyExists", http_status_code=409)
        with pytest.raises(ClientError):
            errors.ensure_bucket(s3, "taken")


def test_put_item_if_absent(dynamodb):
    assert errors.put_item_if_absent(dynamodb, "items", {"id": {"S": "1"}, "v": {"N": "1"}}, "id") is True
    assert errors.put_item_if_absent(dynamodb, "items", {"id": {"S": "1"}, "v": {"N": "2"}}, "id") is False
    assert dynamodb.get_item(TableName="items", Key={"id": {"S": "1"}})["Item"]["v"] == {"N": "1"}


def test_param_validation_is_local(s3):
    with pytest.raises(ParamValidationError) as excinfo:
        s3.get_object(Bucket="test-bucket")
    assert errors.classify(excinfo.value) == "local"
