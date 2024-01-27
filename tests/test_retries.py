import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from patterns.retries import backoff_delay, call_with_backoff, client_config

from conftest import stub_client


def test_client_config_is_applied_to_the_client():
    import boto3
    config = client_config(total_attempts=7, mode="adaptive", connect_timeout=2, read_timeout=9,
                           max_pool_connections=32)
    client = boto3.client("sqs", region_name="us-east-1", aws_access_key_id="testing",
                          aws_secret_access_key="testing", config=config)
    applied = client.meta.config
    assert applied.retries["mode"] == "adaptive"
    assert applied.retries["total_max_attempts"] == 7
    assert (applied.connect_timeout, applied.read_timeout, applied.max_pool_connections) == (2, 9, 32)


def test_backoff_delay_doubles_up_to_the_cap():
    ceilings = [backoff_delay(n, base=1, cap=10, rng=lambda: 1.0) for n in range(1, 7)]
    assert ceilings == [1, 2, 4, 8, 10, 10]
    assert backoff_delay(3, base=1, cap=10, rng=lambda: 0.25) == 1.0
    assert all(0 <= backoff_delay(4) <= 4.0 for _ in range(200))


def test_call_with_backoff_retries_listed_codes_then_succeeds():
    client = stub_client("dynamodb")
    waits = []
    with Stubber(client) as stub:
        stub.add_client_error("describe_table", service_error_code="ResourceNotFoundException")
        stub.add_client_error("describe_table", service_error_code="ResourceNotFoundException")
        stub.add_response("describe_table", {"Table": {"TableStatus": "ACTIVE"}})
        result = call_with_backoff(lambda: client.describe_table(TableName="t"),
                                   {"ResourceNotFoundException"}, sleep=waits.append, rng=lambda: 1.0)
        stub.assert_no_pending_responses()
    assert result["Table"]["TableStatus"] == "ACTIVE"
    assert waits == [0.5, 1.0]


def test_call_with_backoff_does_not_retry_other_codes():
    client = stub_client("dynamodb")
    waits = []
    with Stubber(client) as stub:
        stub.add_client_error("describe_table", service_error_code="AccessDeniedException")
        with pytest.raises(ClientError) as excinfo:
            call_with_backoff(lambda: client.describe_table(TableName="t"),
                              {"ResourceNotFoundException"}, sleep=waits.append)
    assert excinfo.value.response["Error"]["Code"] == "AccessDeniedException"
    assert waits == []


def test_call_with_backoff_gives_up_after_max_attempts():
    client = stub_client("dynamodb")
    waits = []
    with Stubber(client) as stub:
        for _ in range(3):
            stub.add_client_error("describe_table", service_error_code="ResourceInUseException")
        with pytest.raises(ClientError):
            call_with_backoff(lambda: client.describe_table(TableName="t"), ["ResourceInUseException"],
                              max_attempts=3, sleep=waits.append, rng=lambda: 1.0)
        stub.assert_no_pending_responses()
    assert len(waits) == 2  # no sleep after the final failure
