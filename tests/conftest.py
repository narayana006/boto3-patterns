import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def aws_env(monkeypatch, tmp_path):
    """Dummy credentials and empty config files, so no test can reach a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def s3(aws):
    client = boto3.client("s3")
    client.create_bucket(Bucket="test-bucket")
    return client


@pytest.fixture
def dynamodb(aws):
    client = boto3.client("dynamodb")
    client.create_table(
        TableName="items", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
    )
    return client


def stub_client(service):
    """A client that never needs the network; pair it with botocore.stub.Stubber."""
    return boto3.client(service, region_name="us-east-1",
                        aws_access_key_id="testing", aws_secret_access_key="testing")
