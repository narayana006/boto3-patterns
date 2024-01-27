"""DynamoDB batch writes: resend ``UnprocessedItems`` until nothing is left.

``BatchWriteItem`` can return HTTP 200 and still not have written
everything. When the table is throttled, the items it skipped come back in
``UnprocessedItems``, and no exception is raised. Code that ignores that key
loses data without any error.

``batch_write`` splits the input into requests of 25 (the API limit), then
resends unprocessed items with exponential backoff.

The higher-level ``Table.batch_writer()`` from ``boto3.resource("dynamodb")``
also buffers and resends unprocessed items for you. Use this version when
you work with the low-level client, or want control over the backoff and a
hard failure after a bounded number of retries.

Run the demo (stubbed and in-memory, no AWS account needed):

    python patterns/dynamodb_batch.py
"""

from __future__ import annotations

import random
import time
from typing import Callable, Iterable

from boto3.dynamodb.types import TypeSerializer

MAX_BATCH = 25  # BatchWriteItem accepts at most 25 put/delete requests per call


class UnprocessedItemsError(RuntimeError):
    """Gave up after the retry limit.

    ``requests`` holds everything that was not written: the unprocessed
    items of the failing batch plus any later batches that were never sent.
    """

    def __init__(self, requests: list[dict], written: int):
        super().__init__(f"gave up after retries: {len(requests)} write request(s) not written, "
                         f"{written} written")
        self.requests = requests
        self.written = written


def to_put_requests(items: Iterable[dict]) -> list[dict]:
    """Turn plain Python dicts into typed ``PutRequest`` entries for the low-level client."""
    serializer = TypeSerializer()
    return [{"PutRequest": {"Item": {name: serializer.serialize(value) for name, value in item.items()}}}
            for item in items]


def batch_write(client, table: str, requests: list[dict], *, max_retries: int = 8, base_delay: float = 0.1,
                max_delay: float = 5.0, sleep: Callable[[float], None] = time.sleep,
                rng: Callable[[], float] = random.random) -> int:
    """Send put/delete requests in batches of 25, retrying unprocessed items.

    ``requests`` is a list of ``{"PutRequest": ...}`` / ``{"DeleteRequest": ...}``
    dicts (see :func:`to_put_requests`). Returns the number of requests
    written. Raises :class:`UnprocessedItemsError` if a batch still has
    unprocessed items after ``max_retries`` resends.

    A single batch must not contain two requests for the same key; DynamoDB
    rejects the whole call with a ValidationException.
    """
    written = 0
    for start in range(0, len(requests), MAX_BATCH):
        pending = requests[start:start + MAX_BATCH]
        retries = 0
        while True:
            response = client.batch_write_item(RequestItems={table: pending})
            unprocessed = response.get("UnprocessedItems", {}).get(table, [])
            written += len(pending) - len(unprocessed)
            if not unprocessed:
                break
            if retries == max_retries:
                raise UnprocessedItemsError(unprocessed + requests[start + MAX_BATCH:], written)
            retries += 1
            sleep(rng() * min(max_delay, base_delay * 2 ** retries))
            pending = unprocessed
    return written


def demo() -> None:
    import os

    import boto3
    from botocore.stub import Stubber
    from moto import mock_aws

    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="us-east-1",
                      AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_CONFIG_FILE=os.devnull)
    os.environ.pop("AWS_PROFILE", None)

    # 1. Against moto: 60 items go out as three requests (25 + 25 + 10).
    with mock_aws():
        client = boto3.client("dynamodb")
        client.create_table(
            TableName="events", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        )
        items = [{"id": f"evt-{n:03d}", "size": n, "tags": ["demo"]} for n in range(60)]
        written = batch_write(client, "events", to_put_requests(items))
        print(f"moto: wrote {written} items, table now holds {client.scan(TableName='events', Select='COUNT')['Count']}")

    # 2. Against a stub that behaves like a throttled table: the first response
    #    hands two of three items back, the second hands one back.
    client = boto3.client("dynamodb", region_name="us-east-1",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    requests = to_put_requests([{"id": "a"}, {"id": "b"}, {"id": "c"}])
    waits = []
    with Stubber(client) as stub:
        stub.add_response("batch_write_item", {"UnprocessedItems": {"events": requests[1:]}},
                          {"RequestItems": {"events": requests}})
        stub.add_response("batch_write_item", {"UnprocessedItems": {"events": requests[2:]}},
                          {"RequestItems": {"events": requests[1:]}})
        stub.add_response("batch_write_item", {"UnprocessedItems": {}},
                          {"RequestItems": {"events": requests[2:]}})
        written = batch_write(client, "events", requests, sleep=waits.append, rng=lambda: 1.0)
    print(f"stub: wrote {written} items in {len(waits) + 1} calls, backing off {waits} seconds between them")


if __name__ == "__main__":
    demo()
