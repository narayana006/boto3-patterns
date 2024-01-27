"""Assumed roles: get a session for another account or a narrower role.

``assume_role_session`` is the simple form: one ``sts:AssumeRole`` call and a
session built from the temporary credentials. Those credentials expire (one
hour by default) and the session does not renew them, which is fine for a
short script and wrong for a long-running process.

``refreshable_session`` returns a session that calls ``AssumeRole`` again
shortly before the credentials expire.

If you only need this for your own shell, a profile with ``role_arn`` and
``source_profile`` in ``~/.aws/config`` gives you automatic refresh with no
code: ``boto3.Session(profile_name="that-profile")``.

Run the demo (in-memory fake, no AWS account needed):

    python patterns/assume_role.py
"""

from __future__ import annotations

from typing import Callable, Iterable, TypeVar

import boto3
import botocore.session
from botocore.credentials import DeferredRefreshableCredentials

T = TypeVar("T")


def _assume(sts, role_arn: str, session_name: str, duration_seconds: int, external_id: str | None) -> dict:
    kwargs = {"RoleArn": role_arn, "RoleSessionName": session_name, "DurationSeconds": duration_seconds}
    if external_id:
        # Required by the role's trust policy when a third party assumes it.
        kwargs["ExternalId"] = external_id
    return sts.assume_role(**kwargs)["Credentials"]


def assume_role_session(role_arn: str, session_name: str, *, base_session: boto3.Session | None = None,
                        region_name: str | None = None, duration_seconds: int = 3600,
                        external_id: str | None = None) -> boto3.Session:
    """Return a session using temporary credentials for ``role_arn``.

    ``session_name`` shows up in CloudTrail and in the assumed-role ARN, so
    make it say who or what is acting (``"nightly-report"``, not ``"session"``).
    The credentials are not renewed when they expire.
    """
    base = base_session or boto3.Session()
    credentials = _assume(base.client("sts"), role_arn, session_name, duration_seconds, external_id)
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region_name or base.region_name,
    )


def refreshable_session(role_arn: str, session_name: str, *, base_session: boto3.Session | None = None,
                        region_name: str | None = None, duration_seconds: int = 3600,
                        external_id: str | None = None, sts_client=None) -> boto3.Session:
    """Return a session whose credentials renew themselves before they expire.

    Nothing is called until the first request needs credentials. Note that
    this assigns ``_credentials`` on a botocore session, which is not public
    API; boto3 has no public way to plug in a refresh callback. The pattern
    is widely used, but pin your botocore version and keep the test.
    """
    base = base_session or boto3.Session()
    sts = sts_client or base.client("sts")

    def refresh() -> dict:
        credentials = _assume(sts, role_arn, session_name, duration_seconds, external_id)
        return {
            "access_key": credentials["AccessKeyId"],
            "secret_key": credentials["SecretAccessKey"],
            "token": credentials["SessionToken"],
            "expiry_time": credentials["Expiration"].isoformat(),
        }

    botocore_session = botocore.session.get_session()
    botocore_session._credentials = DeferredRefreshableCredentials(
        refresh_using=refresh, method="sts-assume-role"
    )
    return boto3.Session(botocore_session=botocore_session, region_name=region_name or base.region_name)


def for_each_account(role_arns: Iterable[str], func: Callable[[boto3.Session], T], *, session_name: str,
                     base_session: boto3.Session | None = None) -> dict[str, T | Exception]:
    """Run ``func(session)`` once per role and return ``{role_arn: result or exception}``.

    A role that cannot be assumed, or a failure inside ``func``, is recorded
    for that role and does not stop the others.
    """
    outcome: dict[str, T | Exception] = {}
    for role_arn in role_arns:
        try:
            session = assume_role_session(role_arn, session_name, base_session=base_session)
            outcome[role_arn] = func(session)
        except Exception as exc:
            outcome[role_arn] = exc
    return outcome


def demo() -> None:
    import os

    from moto import mock_aws

    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION="us-east-1",
                      AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_CONFIG_FILE=os.devnull)
    os.environ.pop("AWS_PROFILE", None)
    os.environ.pop("AWS_SESSION_TOKEN", None)
    with mock_aws():
        role = "arn:aws:iam::123456789012:role/read-only-audit"
        session = assume_role_session(role, "pattern-demo")
        print("assume_role_session identity:", session.client("sts").get_caller_identity()["Arn"])

        refreshing = refreshable_session(role, "pattern-demo-refresh")
        print("refreshable_session identity:", refreshing.client("sts").get_caller_identity()["Arn"])
        print("credential method:", refreshing.get_credentials().method)

        roles = [f"arn:aws:iam::{account}:role/read-only-audit" for account in ("123456789012", "210987654321")]
        outcome = for_each_account(
            roles, lambda s: s.client("sts").get_caller_identity()["Account"], session_name="pattern-demo")
        for role_arn, account in outcome.items():
            print(f"for_each_account: {role_arn} -> account {account}")


if __name__ == "__main__":
    demo()
