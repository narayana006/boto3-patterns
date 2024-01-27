import threading

import boto3

from patterns.concurrency import object_sizes, pooled_client, run_concurrently


def test_run_concurrently_separates_results_and_errors():
    def work(n):
        if n % 5 == 0:
            raise ValueError(f"bad {n}")
        return n * n

    results, errors = run_concurrently(work, range(1, 21), max_workers=4)
    assert results == {n: n * n for n in range(1, 21) if n % 5}
    assert sorted(errors) == [5, 10, 15, 20]
    assert str(errors[10]) == "bad 10"


def test_run_concurrently_really_uses_several_threads():
    barrier = threading.Barrier(3, timeout=5)  # only passes if 3 calls overlap

    def work(n):
        barrier.wait()
        return threading.get_ident()

    results, errors = run_concurrently(work, range(3), max_workers=3)
    assert not errors and len(set(results.values())) == 3


def test_run_concurrently_with_no_items():
    assert run_concurrently(lambda x: x, []) == ({}, {})


def test_pooled_client_sizes_the_connection_pool(aws):
    client = pooled_client(boto3.Session(), "s3", max_workers=24)
    assert client.meta.config.max_pool_connections == 24
    assert client.meta.config.retries["mode"] == "standard"


def test_object_sizes_with_one_shared_client(s3):
    keys = [f"obj-{i}" for i in range(20)]
    for i, key in enumerate(keys):
        s3.put_object(Bucket="test-bucket", Key=key, Body=b"x" * i)
    sizes, errors = object_sizes(s3, "test-bucket", keys + ["absent"], max_workers=8)
    assert sizes == {key: i for i, key in enumerate(keys)}
    assert list(errors) == ["absent"]
    assert errors["absent"].response["Error"]["Code"] == "404"
