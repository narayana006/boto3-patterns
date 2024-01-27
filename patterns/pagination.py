"""Pagination: read every page, not just the first one.

Most list/describe operations return one page per call. Code such as
``s3.list_objects_v2(Bucket=b)["Contents"]`` looks right in a small test
account and silently drops everything after the first page in a large one.

Run the demo (in-memory fake, no AWS account needed):

    python patterns/pagination.py
"""

from __future__ import annotations

from typing import Any, Iterator


def iter_items(client, operation: str, result_key: str, *, page_size: int | None = None,
               max_items: int | None = None, **params) -> Iterator[Any]:
    """Yield every item from a paginated operation.

    ``operation`` is the snake_case method name (``"list_objects_v2"``) and
    ``result_key`` the response key holding the list (``"Contents"``).

    ``page.get(result_key, [])`` matters: S3, for one, omits ``Contents``
    entirely when a page is empty, so ``page[result_key]`` raises KeyError on
    an empty bucket.
    """
    pagination_config = {}
    if page_size is not None:
        pagination_config["PageSize"] = page_size    # items per API call
    if max_items is not None:
        pagination_config["MaxItems"] = max_items    # stop after this many in total
    paginator = client.get_paginator(operation)
    for page in paginator.paginate(**params, PaginationConfig=pagination_config):
        yield from page.get(result_key, [])


def search_items(client, operation: str, expression: str, **params) -> Iterator[Any]:
    """Yield the results of a JMESPath expression applied to every page.

    Filtering happens client-side, after each page has been downloaded; it
    does not reduce the number of API calls. When the expression matches
    nothing on a page, botocore yields ``None`` for that page, so ``None``
    is skipped here.
    """
    paginator = client.get_paginator(operation)
    for item in paginator.paginate(**params).search(expression):
        if item is not None:
            yield item


def iter_items_by_token(call, result_key: str, *, token_param: str = "NextToken",
                        token_key: str = "NextToken", **params) -> Iterator[Any]:
    """Token loop for operations that have no paginator.

    Check first with ``client.can_paginate("operation_name")``; prefer
    :func:`iter_items` when it returns True. ``call`` is the bound client
    method. The loop ends when the response carries no token. An empty page
    is not the end: some services return empty pages that still have a token.
    """
    token = None
    while True:
        kwargs = dict(params)
        if token:
            kwargs[token_param] = token
        response = call(**kwargs)
        yield from response.get(result_key, [])
        token = response.get(token_key)
        if not token:
            return


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
        for i in range(1234):
            s3.put_object(Bucket="demo-bucket", Key=f"logs/{i:04d}.txt", Body=b"x" * (i % 7))

        first_page = s3.list_objects_v2(Bucket="demo-bucket")
        print(f"single list_objects_v2 call: {len(first_page['Contents'])} keys "
              f"(IsTruncated={first_page['IsTruncated']})")
        everything = list(iter_items(s3, "list_objects_v2", "Contents", Bucket="demo-bucket"))
        print(f"iter_items with a paginator: {len(everything)} keys")
        capped = list(iter_items(s3, "list_objects_v2", "Contents", Bucket="demo-bucket",
                                 page_size=100, max_items=250))
        print(f"page_size=100, max_items=250: {len(capped)} keys")
        empty = list(search_items(s3, "list_objects_v2", "Contents[?Size == `0`].Key", Bucket="demo-bucket"))
        print(f"search_items for zero-byte objects: {len(empty)} keys, first is {empty[0]}")


if __name__ == "__main__":
    demo()
