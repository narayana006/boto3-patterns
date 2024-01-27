"""Waiters: poll until a resource reaches a state, with a bounded wait.

``wait_for_object`` uses a waiter that ships with boto3. ``wait_until_queue_empty``
builds a custom waiter for a condition boto3 has no waiter for, using the same
machinery (``botocore.waiter``), so you get the polling loop, the attempt
limit and ``WaiterError`` without writing a ``while True: sleep`` loop.

Run the demo (in-memory fake, no AWS account needed):

    python patterns/waiters.py
"""

from __future__ import annotations

from botocore.exceptions import WaiterError
from botocore.waiter import WaiterModel, create_waiter_with_client


def wait_for_object(s3, bucket: str, key: str, *, delay: float = 2, max_attempts: int = 15) -> bool:
    """Wait for an S3 object to exist. Returns False if it did not appear in time.

    Every built-in waiter has its own default delay and attempt count;
    ``WaiterConfig`` overrides them per call. The total wait is roughly
    ``delay * max_attempts`` seconds. ``s3.waiter_names`` lists the waiters a
    client has.
    """
    waiter = s3.get_waiter("object_exists")
    try:
        waiter.wait(Bucket=bucket, Key=key, WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts})
    except WaiterError:
        return False
    return True


def queue_empty_waiter(sqs, *, delay: float = 5, max_attempts: int = 24):
    """Build a waiter that succeeds when a queue reports no messages.

    A waiter model names the operation to poll and a list of acceptors. Each
    acceptor is checked against every response; here a JMESPath expression
    must evaluate to ``true``. Responses that match no acceptor mean "keep
    polling". An API error that no acceptor matches ends the wait with a
    ``WaiterError``.

    The SQS counts are approximate, so treat success as "the queue looked
    empty", not as a guarantee.
    """
    model = WaiterModel({
        "version": 2,
        "waiters": {
            "QueueEmpty": {
                "operation": "GetQueueAttributes",
                "delay": delay,
                "maxAttempts": max_attempts,
                "acceptors": [{
                    "matcher": "path",
                    "argument": ("Attributes.ApproximateNumberOfMessages == '0' && "
                                 "Attributes.ApproximateNumberOfMessagesNotVisible == '0'"),
                    "expected": True,
                    "state": "success",
                }],
            }
        },
    })
    return create_waiter_with_client("QueueEmpty", model, sqs)


def wait_until_queue_empty(sqs, queue_url: str, *, delay: float = 5, max_attempts: int = 24) -> None:
    """Block until the queue has no visible or in-flight messages; raise WaiterError on timeout."""
    queue_empty_waiter(sqs, delay=delay, max_attempts=max_attempts).wait(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )


def demo() -> None:
    import os

    import boto3
    from moto import mock_aws

    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="us-east-1",
                      AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_CONFIG_FILE=os.devnull)
    os.environ.pop("AWS_PROFILE", None)
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="demo-bucket")
        print("object_exists before upload:",
              wait_for_object(s3, "demo-bucket", "report.csv", delay=0.01, max_attempts=3))
        s3.put_object(Bucket="demo-bucket", Key="report.csv", Body=b"a,b\n")
        print("object_exists after upload: ",
              wait_for_object(s3, "demo-bucket", "report.csv", delay=0.01, max_attempts=3))

        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName="demo-queue")["QueueUrl"]
        for n in range(3):
            sqs.send_message(QueueUrl=queue_url, MessageBody=f"job {n}")
        try:
            wait_until_queue_empty(sqs, queue_url, delay=0.01, max_attempts=3)
        except WaiterError as exc:
            print(f"queue with 3 messages: WaiterError ({exc.kwargs['reason']})")
        for message in sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)["Messages"]:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
        wait_until_queue_empty(sqs, queue_url, delay=0.01, max_attempts=3)
        print("queue after consuming the messages: empty, waiter returned")


if __name__ == "__main__":
    demo()
