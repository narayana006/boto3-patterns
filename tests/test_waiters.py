import boto3
import pytest
from botocore.exceptions import WaiterError
from botocore.stub import Stubber

from patterns.waiters import queue_empty_waiter, wait_for_object, wait_until_queue_empty

from conftest import stub_client

ATTRS = ["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"]


def test_wait_for_object(s3):
    assert wait_for_object(s3, "test-bucket", "late.txt", delay=0, max_attempts=2) is False
    s3.put_object(Bucket="test-bucket", Key="late.txt", Body=b"1")
    assert wait_for_object(s3, "test-bucket", "late.txt", delay=0, max_attempts=2) is True


def attrs(visible, in_flight):
    return {"Attributes": {ATTRS[0]: str(visible), ATTRS[1]: str(in_flight)}}


def test_custom_waiter_polls_until_both_counts_are_zero():
    sqs = stub_client("sqs")
    expected = {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/123456789012/q", "AttributeNames": ATTRS}
    with Stubber(sqs) as stub:
        stub.add_response("get_queue_attributes", attrs(3, 0), expected)
        stub.add_response("get_queue_attributes", attrs(0, 2), expected)  # still in flight
        stub.add_response("get_queue_attributes", attrs(0, 0), expected)
        wait_until_queue_empty(sqs, expected["QueueUrl"], delay=0, max_attempts=5)
        stub.assert_no_pending_responses()


def test_custom_waiter_times_out():
    sqs = stub_client("sqs")
    with Stubber(sqs) as stub:
        for _ in range(2):
            stub.add_response("get_queue_attributes", attrs(1, 0))
        with pytest.raises(WaiterError) as excinfo:
            wait_until_queue_empty(sqs, "https://sqs.us-east-1.amazonaws.com/123456789012/q",
                                   delay=0, max_attempts=2)
    assert "Max attempts exceeded" in str(excinfo.value)


def test_custom_waiter_stops_on_api_error():
    sqs = stub_client("sqs")
    with Stubber(sqs) as stub:
        stub.add_client_error("get_queue_attributes", service_error_code="AccessDenied")
        with pytest.raises(WaiterError):
            wait_until_queue_empty(sqs, "https://sqs.us-east-1.amazonaws.com/123456789012/q", delay=0)


def test_custom_waiter_against_moto(aws):
    sqs = boto3.client("sqs")
    url = sqs.create_queue(QueueName="jobs")["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody="one")
    with pytest.raises(WaiterError):
        wait_until_queue_empty(sqs, url, delay=0, max_attempts=2)
    message = sqs.receive_message(QueueUrl=url)["Messages"][0]
    sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
    wait_until_queue_empty(sqs, url, delay=0, max_attempts=2)


def test_waiter_model_settings():
    waiter = queue_empty_waiter(stub_client("sqs"), delay=7, max_attempts=3)
    assert waiter.name == "QueueEmpty"
    assert (waiter.config.delay, waiter.config.max_attempts) == (7, 3)
