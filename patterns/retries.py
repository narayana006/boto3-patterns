"""Retries and backoff: configure botocore's retries, and add your own where it stops.

Two layers:

1. ``client_config`` sets botocore's built-in retry behaviour. botocore
   already retries throttling errors, transient 5xx errors and connection
   problems; you only choose the mode, the attempt limit and the timeouts.
2. ``call_with_backoff`` is for errors botocore does not retry because they
   are not transient from its point of view, but are worth retrying in your
   workflow (a resource that is still being created, an optimistic-lock
   conflict, an eventually consistent read).

Run the demo (stubbed responses, no AWS account needed):

    python patterns/retries.py
"""

from __future__ import annotations

import random
import time
from typing import Callable, Iterable, TypeVar

from botocore.config import Config
from botocore.exceptions import ClientError

T = TypeVar("T")


def client_config(*, total_attempts: int = 5, mode: str = "standard", connect_timeout: float = 5,
                  read_timeout: float = 30, max_pool_connections: int = 10) -> Config:
    """A ``Config`` with explicit retries and timeouts.

    * ``mode``: ``"standard"`` (exponential backoff with jitter and a retry
      quota) or ``"adaptive"`` (standard plus client-side rate limiting).
      When no mode is set botocore uses ``"legacy"``.
    * ``total_attempts`` counts the first call, so 5 means up to 4 retries.
      It is passed as ``total_max_attempts``; the similarly named
      ``max_attempts`` key does not count the first call.
    * botocore's default connect and read timeouts are 60 seconds each,
      which is long for most interactive or Lambda code.

    Use it as ``session.client("dynamodb", config=client_config())``.
    """
    return Config(
        retries={"mode": mode, "total_max_attempts": total_attempts},
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        max_pool_connections=max_pool_connections,
    )


def backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 20.0,
                  rng: Callable[[], float] = random.random) -> float:
    """Exponential backoff with full jitter.

    ``attempt`` starts at 1. The delay is a random value between 0 and
    ``min(cap, base * 2 ** (attempt - 1))``. The randomness spreads out
    clients that all failed at the same moment.
    """
    return rng() * min(cap, base * 2 ** (attempt - 1))


def call_with_backoff(func: Callable[[], T], retry_on: Iterable[str], *, max_attempts: int = 5,
                      base: float = 0.5, cap: float = 20.0,
                      sleep: Callable[[float], None] = time.sleep,
                      rng: Callable[[], float] = random.random) -> T:
    """Call ``func`` and retry when it raises a ClientError whose code is in ``retry_on``.

    Any other error, and the last failure once ``max_attempts`` is reached,
    is re-raised unchanged. Only use this around calls that are safe to
    repeat.
    """
    codes = set(retry_on)
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in codes or attempt == max_attempts:
                raise
            sleep(backoff_delay(attempt, base=base, cap=cap, rng=rng))
    raise AssertionError("unreachable")  # pragma: no cover


def demo() -> None:
    import boto3
    from botocore.stub import Stubber

    config = client_config(total_attempts=6, mode="adaptive", read_timeout=10)
    print(f"client_config: retries={config.retries} connect_timeout={config.connect_timeout} "
          f"read_timeout={config.read_timeout}")

    # A stubbed client: the first two calls fail, the third succeeds.
    dynamodb = boto3.client("dynamodb", region_name="us-east-1", aws_access_key_id="testing",
                            aws_secret_access_key="testing", config=config)
    with Stubber(dynamodb) as stub:
        stub.add_client_error("describe_table", service_error_code="ResourceNotFoundException")
        stub.add_client_error("describe_table", service_error_code="ResourceNotFoundException")
        stub.add_response("describe_table", {"Table": {"TableName": "orders", "TableStatus": "ACTIVE"}})
        waits = []
        result = call_with_backoff(
            lambda: dynamodb.describe_table(TableName="orders"),
            retry_on={"ResourceNotFoundException"},
            sleep=waits.append,          # record instead of sleeping, to keep the demo instant
            rng=lambda: 1.0,             # no jitter, so the output is the same on every run
        )
    print(f"call_with_backoff: succeeded after {len(waits) + 1} attempts, "
          f"table status {result['Table']['TableStatus']}, waits would have been {waits} seconds")
    print("backoff ceiling per attempt (base 0.5s, cap 20s):",
          [backoff_delay(n, rng=lambda: 1.0) for n in range(1, 9)])


if __name__ == "__main__":
    demo()
