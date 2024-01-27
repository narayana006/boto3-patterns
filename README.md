# boto3-patterns

Small, runnable, tested boto3 examples of the things most AWS scripts get subtly wrong: pagination, retries, waiters, error handling, threads, assumed roles, S3 multipart uploads and DynamoDB batch writes.

**Status:** eight patterns work today. Each one is a single file with a demo you can run without an AWS account and a test file. Everything has been run against [moto](https://github.com/getmoto/moto) (an in-memory fake of AWS) and `botocore` stubs only, not against a real account.

## Quick start

```bash
git clone https://github.com/narayana006/boto3-patterns && cd boto3-patterns
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python patterns/pagination.py
```

Output (the demo creates 1,234 objects in a moto bucket; nothing touches AWS):

```
single list_objects_v2 call: 1000 keys (IsTruncated=True)
iter_items with a paginator: 1234 keys
page_size=100, max_items=250: 250 keys
search_items for zero-byte objects: 177 keys, first is logs/0000.txt
```

The first line is the bug this pattern exists for: one call returns one page.

To use the helpers from your own code without cloning, install the package:

```bash
pip install git+https://github.com/narayana006/boto3-patterns
```

and `from patterns.pagination import iter_items`. Copying the one file you need is just as good; no module imports another.

## The patterns

| File | What it shows | Tested with |
| --- | --- | --- |
| [`patterns/pagination.py`](patterns/pagination.py) | `iter_items` (paginators, `PageSize`, `MaxItems`), `search_items` (JMESPath over pages), `iter_items_by_token` (manual token loop for operations without a paginator) | moto, Stubber |
| [`patterns/retries.py`](patterns/retries.py) | `client_config` (retry mode, attempt limit, timeouts), `backoff_delay` (exponential backoff with full jitter), `call_with_backoff` (retry error codes botocore does not retry) | Stubber |
| [`patterns/waiters.py`](patterns/waiters.py) | `wait_for_object` (built-in waiter with `WaiterConfig`), `queue_empty_waiter` (a custom waiter built from a `WaiterModel`) | moto, Stubber |
| [`patterns/errors.py`](patterns/errors.py) | `get_object_or_none`, `object_exists`, `ensure_bucket`, `put_item_if_absent`, and `classify` (throttled / access / not_found / client / server / local) | moto, Stubber |
| [`patterns/concurrency.py`](patterns/concurrency.py) | `run_concurrently` (thread pool, results and errors per item), `pooled_client` (connection pool sized to the workers) | moto |
| [`patterns/assume_role.py`](patterns/assume_role.py) | `assume_role_session`, `refreshable_session` (credentials that renew themselves), `for_each_account` | moto, Stubber |
| [`patterns/s3_multipart.py`](patterns/s3_multipart.py) | `upload_file_multipart` (managed transfer), `upload_parts` (manual parts with abort on failure), `find_incomplete_uploads`, `abort_incomplete_uploads` | moto, Stubber |
| [`patterns/dynamodb_batch.py`](patterns/dynamodb_batch.py) | `batch_write` (batches of 25, resends `UnprocessedItems` with backoff, fails loudly when it gives up) | moto, Stubber |

Each file starts with a docstring explaining the mistake it avoids, and every function documents the AWS behaviour it depends on.

## Running the demos

Run one file directly, or use the runner:

```bash
python patterns/dynamodb_batch.py     # one demo
python -m patterns                    # list the patterns
python -m patterns retries waiters    # run some
python -m patterns all                # run all eight
```

Every demo sets dummy credentials and uses moto or a `botocore` Stubber, so it cannot reach a real account. This is the output of `python -m patterns all`:

```
== pagination ==
single list_objects_v2 call: 1000 keys (IsTruncated=True)
iter_items with a paginator: 1234 keys
page_size=100, max_items=250: 250 keys
search_items for zero-byte objects: 177 keys, first is logs/0000.txt

== retries ==
client_config: retries={'mode': 'adaptive', 'total_max_attempts': 6} connect_timeout=5 read_timeout=10
call_with_backoff: succeeded after 3 attempts, table status ACTIVE, waits would have been [0.5, 1.0] seconds
backoff ceiling per attempt (base 0.5s, cap 20s): [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 20.0, 20.0]

== waiters ==
object_exists before upload: False
object_exists after upload:  True
queue with 3 messages: WaiterError (Max attempts exceeded)
queue after consuming the messages: empty, waiter returned

== errors ==
ensure_bucket, first call:  True
ensure_bucket, second call: False
get_object_or_none(present.txt): b'hello'
get_object_or_none(missing.txt): None
object_exists(missing.txt): False
get_object on a missing bucket: NoSuchBucket code=NoSuchBucket status=404 -> not_found
head_object on a missing key: ClientError code=404 status=404 -> not_found
get_object without a Key: ParamValidationError code=None status=None -> local
put_item_if_absent, first call:  True
put_item_if_absent, second call: False

== concurrency ==
pool size: 16 connections for 16 workers
40 objects sized, 820 bytes in total
failed: data/missing: ClientError (404)

== assume_role ==
assume_role_session identity: arn:aws:sts::123456789012:assumed-role/read-only-audit/pattern-demo
refreshable_session identity: arn:aws:sts::123456789012:assumed-role/read-only-audit/pattern-demo-refresh
credential method: sts-assume-role
for_each_account: arn:aws:iam::123456789012:role/read-only-audit -> account 123456789012
for_each_account: arn:aws:iam::210987654321:role/read-only-audit -> account 210987654321

== s3_multipart ==
upload_file_multipart: 12582912 bytes, ETag "4e982d58b6c2ce178ae042c23f9bca6e-3"
upload_parts: 3 parts, 10486784 bytes, ETag "c43647d2543bbb0984bac6210ff0eb6a-3"
upload_parts with a failing source: source stream closed; uploads left behind: 0
abort_incomplete_uploads dry run: would abort ['abandoned/video.mp4']
abort_incomplete_uploads apply=True: uploads left: 0

== dynamodb_batch ==
moto: wrote 60 items, table now holds 60
stub: wrote 3 items in 3 calls, backing off [0.2, 0.4] seconds between them
```

## Using a pattern against real AWS

The functions take a boto3 client or session as an argument and do not create credentials themselves, so the same code runs against a real account:

```python
import boto3
from patterns.pagination import iter_items
from patterns.retries import client_config

ec2 = boto3.client("ec2", config=client_config(total_attempts=8))
for reservation in iter_items(ec2, "describe_instances", "Reservations"):
    ...
```

IAM actions each helper needs when used for real:

| Helper | Actions |
| --- | --- |
| `iter_items`, `search_items`, `iter_items_by_token` | whatever the operation you pass needs (for the demo's `list_objects_v2`: `s3:ListBucket`) |
| `wait_for_object`, `object_exists` | `s3:GetObject` (and `s3:ListBucket` to get a 404 instead of a 403 for a missing key) |
| `wait_until_queue_empty` | `sqs:GetQueueAttributes` |
| `get_object_or_none` | `s3:GetObject` |
| `ensure_bucket` | `s3:CreateBucket` |
| `put_item_if_absent` | `dynamodb:PutItem` |
| `assume_role_session`, `refreshable_session`, `for_each_account` | `sts:AssumeRole` on the target role, and a trust policy on that role that allows the caller |
| `upload_file_multipart`, `upload_parts` | `s3:PutObject`, `s3:AbortMultipartUpload` |
| `find_incomplete_uploads` | `s3:ListBucketMultipartUploads` |
| `abort_incomplete_uploads` | `s3:ListBucketMultipartUploads`; with `apply=True` also `s3:AbortMultipartUpload` |
| `batch_write` | `dynamodb:BatchWriteItem` |

Three helpers change resources when called: `ensure_bucket`, `put_item_if_absent` and the uploads create things, by design. The only helper that removes anything is `abort_incomplete_uploads`, and it is a dry run unless you pass `apply=True`.

## A combined example

[`examples/bucket_report.py`](examples/bucket_report.py) uses three patterns together: list every key, HEAD the objects on a thread pool, and count content types. Output of `python examples/bucket_report.py` (against moto):

```
example-reports: 1500 objects
  application/json           500
  image/png                  500
  text/csv                   500
```

## Tests

```bash
pytest
```

69 tests. `tests/conftest.py` sets dummy credentials and points the AWS config files at empty paths for every test. The tests are themselves the "testing without an AWS account" pattern:

- **moto** when the behaviour of the service matters (real pagination over 1,005 keys, multipart assembly, conditional writes).
- **`botocore.stub.Stubber`** when you need a response moto will not produce on demand: `UnprocessedItems`, credentials about to expire, a throttling or access-denied error, an exact check of the parameters sent.
- **Injected `sleep` and `rng`** arguments so backoff is tested without waiting and without randomness.

## Checklist

- [x] Pagination done correctly
- [x] Retry and backoff configuration
- [x] Waiters and custom waiters
- [x] Safe concurrency with threads
- [x] Cross-account access with assumed roles
- [x] Testing with stubbed clients, no AWS account needed
- [x] Error handling
- [x] S3 multipart uploads and cleanup of incomplete uploads
- [x] DynamoDB batch writes with unprocessed-item retry
- [ ] SQS consumer loop (long polling, visibility timeout, partial batch failures)
- [ ] Async variants
- [ ] Presigned URLs

## Limitations

- Nothing here has been run against a real AWS account. moto and Stubber model the API shapes, not every service behaviour, so treat timing, throttling and permission behaviour as described by the AWS documentation rather than proven by these tests.
- botocore's own retry loop is not exercised by the tests, because a Stubber answers before the retry handler runs. `client_config` is tested only by checking the settings on the client.
- `refreshable_session` assigns a private botocore attribute (`_credentials`). boto3 offers no public hook for this; the tests will tell you if a botocore release breaks it.
- moto reports a fixed start time for multipart uploads, so the age filter in `find_incomplete_uploads` is tested with a Stubber instead.
- These are patterns, not a framework: there is no stable API promise, and the helpers cover the common case rather than every option of the underlying call.

## Contributing

A new pattern is welcome if it is one file, explains the mistake it prevents, has a `demo()` that runs offline, and comes with a test that uses moto or a Stubber. Corrections to anything said about AWS behaviour are especially welcome; please link the documentation.

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
