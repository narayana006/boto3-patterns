"""Concurrency with threads: share a client, size the connection pool, collect errors per item.

Rules this pattern follows:

* Low-level clients are thread safe, so create one client and share it
  across threads. ``Session`` objects and resource objects (``boto3.resource``)
  are not thread safe; give each thread its own if you need them.
* A client keeps a pool of 10 HTTP connections by default. With more worker
  threads than that, the extra connections are opened and then thrown away
  after each request (urllib3 logs "Connection pool is full, discarding
  connection"), so those threads pay for a new TLS handshake every time.
  Set ``max_pool_connections`` to the number of workers.
* One failed item should not lose the results of the others, so results and
  errors are returned separately, keyed by item.

Run the demo (in-memory fake, no AWS account needed):

    python patterns/concurrency.py
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Hashable, Iterable, TypeVar

from botocore.config import Config

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


def pooled_client(session, service: str, max_workers: int, **client_kwargs):
    """Create a client whose connection pool matches the number of worker threads."""
    config = Config(max_pool_connections=max_workers, retries={"mode": "standard"})
    return session.client(service, config=config, **client_kwargs)


def run_concurrently(func: Callable[[K], V], items: Iterable[K], *,
                     max_workers: int = 8) -> tuple[dict[K, V], dict[K, Exception]]:
    """Call ``func(item)`` for every item on a thread pool.

    Returns ``(results, errors)``: two dicts keyed by item. Threads suit
    boto3 calls because the time is spent waiting on the network.
    ``KeyboardInterrupt`` and other non-``Exception`` errors are not swallowed.
    """
    results: dict[K, V] = {}
    errors: dict[K, Exception] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(func, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item] = future.result()
            except Exception as exc:
                errors[item] = exc
    return results, errors


def object_sizes(s3, bucket: str, keys: Iterable[str], *, max_workers: int = 8):
    """Example use: HEAD many objects in parallel with one shared client."""
    return run_concurrently(
        lambda key: s3.head_object(Bucket=bucket, Key=key)["ContentLength"], keys, max_workers=max_workers
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
        s3 = pooled_client(boto3.Session(), "s3", max_workers=16)
        s3.create_bucket(Bucket="demo-bucket")
        keys = [f"data/part-{i:03d}" for i in range(40)]
        for i, key in enumerate(keys):
            s3.put_object(Bucket="demo-bucket", Key=key, Body=b"x" * (i + 1))

        sizes, errors = object_sizes(s3, "demo-bucket", keys + ["data/missing"], max_workers=16)
        print(f"pool size: {s3.meta.config.max_pool_connections} connections for 16 workers")
        print(f"{len(sizes)} objects sized, {sum(sizes.values())} bytes in total")
        for key, exc in errors.items():
            print(f"failed: {key}: {type(exc).__name__} ({exc.response['Error']['Code']})")


if __name__ == "__main__":
    demo()
