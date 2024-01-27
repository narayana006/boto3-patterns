import pytest
from botocore.stub import Stubber

from patterns.dynamodb_batch import UnprocessedItemsError, batch_write, to_put_requests

from conftest import stub_client


def test_to_put_requests_serializes_python_types():
    assert to_put_requests([{"id": "a", "n": 3, "ok": True, "tags": ["x"], "none": None}]) == [{
        "PutRequest": {"Item": {
            "id": {"S": "a"}, "n": {"N": "3"}, "ok": {"BOOL": True}, "tags": {"L": [{"S": "x"}]},
            "none": {"NULL": True},
        }}
    }]


def test_batch_write_chunks_into_25(dynamodb):
    calls = []
    dynamodb.meta.events.register(
        "before-call.dynamodb.BatchWriteItem",
        lambda params, **kwargs: calls.append(1))
    requests = to_put_requests({"id": f"i{n:03d}", "n": n} for n in range(60))
    assert batch_write(dynamodb, "items", requests) == 60
    assert len(calls) == 3
    assert dynamodb.scan(TableName="items", Select="COUNT")["Count"] == 60


def test_batch_write_supports_deletes(dynamodb):
    batch_write(dynamodb, "items", to_put_requests([{"id": "a"}, {"id": "b"}]))
    assert batch_write(dynamodb, "items", [{"DeleteRequest": {"Key": {"id": {"S": "a"}}}}]) == 1
    assert dynamodb.scan(TableName="items", Select="COUNT")["Count"] == 1


def test_batch_write_with_nothing_to_do_makes_no_call():
    client = stub_client("dynamodb")
    with Stubber(client):  # any call would fail: no responses queued
        assert batch_write(client, "items", []) == 0


def test_batch_write_resends_only_unprocessed_items_with_backoff():
    client = stub_client("dynamodb")
    requests = to_put_requests([{"id": "a"}, {"id": "b"}, {"id": "c"}])
    waits = []
    with Stubber(client) as stub:
        stub.add_response("batch_write_item", {"UnprocessedItems": {"items": requests[1:]}},
                          {"RequestItems": {"items": requests}})
        stub.add_response("batch_write_item", {"UnprocessedItems": {"items": requests[2:]}},
                          {"RequestItems": {"items": requests[1:]}})
        stub.add_response("batch_write_item", {"UnprocessedItems": {}},
                          {"RequestItems": {"items": requests[2:]}})
        written = batch_write(client, "items", requests, base_delay=1, max_delay=3,
                              sleep=waits.append, rng=lambda: 1.0)
        stub.assert_no_pending_responses()
    assert written == 3
    assert waits == [2, 3]  # doubles, then hits max_delay


def test_batch_write_gives_up_and_reports_what_is_left():
    client = stub_client("dynamodb")
    requests = to_put_requests({"id": f"i{n}"} for n in range(30))
    stuck = requests[20:25]
    with Stubber(client) as stub:
        for _ in range(3):  # first try + 2 retries, the same 5 items never go through
            stub.add_response("batch_write_item", {"UnprocessedItems": {"items": stuck}})
        with pytest.raises(UnprocessedItemsError) as excinfo:
            batch_write(client, "items", requests, max_retries=2, sleep=lambda s: None)
        stub.assert_no_pending_responses()
    assert excinfo.value.written == 20
    assert excinfo.value.requests == stuck + requests[25:]  # stuck items plus the batch never sent
