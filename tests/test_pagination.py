from botocore.stub import Stubber

from patterns.pagination import iter_items, iter_items_by_token, search_items

from conftest import stub_client


def fill(s3, count):
    for i in range(count):
        s3.put_object(Bucket="test-bucket", Key=f"k/{i:04d}", Body=b"x" * (i % 3))


def test_iter_items_reads_past_the_first_page(s3):
    fill(s3, 1005)  # one more page than a single ListObjectsV2 call returns
    assert len(s3.list_objects_v2(Bucket="test-bucket")["Contents"]) == 1000
    keys = [obj["Key"] for obj in iter_items(s3, "list_objects_v2", "Contents", Bucket="test-bucket")]
    assert len(keys) == 1005 and keys == sorted(set(keys))


def test_iter_items_on_empty_bucket_yields_nothing(s3):
    assert list(iter_items(s3, "list_objects_v2", "Contents", Bucket="test-bucket")) == []


def test_page_size_and_max_items(s3):
    fill(s3, 25)
    calls = []
    s3.meta.events.register("before-call.s3.ListObjectsV2", lambda **kwargs: calls.append(1))
    items = list(iter_items(s3, "list_objects_v2", "Contents", Bucket="test-bucket", page_size=10, max_items=15))
    assert len(items) == 15
    assert len(calls) == 2


def test_search_items_filters_and_skips_none(s3):
    assert list(search_items(s3, "list_objects_v2", "Contents[?Size == `0`].Key", Bucket="test-bucket")) == []
    fill(s3, 7)
    empty = list(search_items(s3, "list_objects_v2", "Contents[?Size == `0`].Key", Bucket="test-bucket"))
    assert empty == ["k/0000", "k/0003", "k/0006"]


def test_iter_items_by_token_follows_tokens_through_an_empty_page():
    ssm = stub_client("ssm")
    with Stubber(ssm) as stub:
        stub.add_response("describe_parameters", {"Parameters": [{"Name": "/a"}], "NextToken": "t1"},
                          {"MaxResults": 1})
        stub.add_response("describe_parameters", {"Parameters": [], "NextToken": "t2"},
                          {"MaxResults": 1, "NextToken": "t1"})
        stub.add_response("describe_parameters", {"Parameters": [{"Name": "/b"}]},
                          {"MaxResults": 1, "NextToken": "t2"})
        names = [p["Name"] for p in iter_items_by_token(ssm.describe_parameters, "Parameters", MaxResults=1)]
        stub.assert_no_pending_responses()
    assert names == ["/a", "/b"]
