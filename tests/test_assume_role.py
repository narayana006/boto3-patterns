from datetime import datetime, timedelta, timezone

from botocore.stub import Stubber

from patterns.assume_role import assume_role_session, for_each_account, refreshable_session

from conftest import stub_client

ROLE = "arn:aws:iam::123456789012:role/reader"


def sts_response(n, expires_in):
    return {
        "Credentials": {
            "AccessKeyId": f"ASIAFAKEFAKEFAKE{n:04d}",
            "SecretAccessKey": f"fake-secret-{n}",
            "SessionToken": f"fake-token-{n}",
            "Expiration": datetime.now(timezone.utc) + expires_in,
        }
    }


def test_assume_role_session_uses_the_role(aws):
    session = assume_role_session(ROLE, "unit-test", region_name="eu-west-1")
    identity = session.client("sts").get_caller_identity()
    assert identity["Arn"] == "arn:aws:sts::123456789012:assumed-role/reader/unit-test"
    assert session.region_name == "eu-west-1"
    assert session.get_credentials().token  # temporary credentials carry a session token


def test_assume_role_passes_external_id_and_duration(monkeypatch):
    import boto3
    sts = stub_client("sts")
    base = boto3.Session(region_name="us-east-1")
    monkeypatch.setattr(base, "client", lambda service: sts)
    with Stubber(sts) as stub:
        stub.add_response("assume_role", sts_response(1, timedelta(hours=1)), {
            "RoleArn": ROLE, "RoleSessionName": "partner", "DurationSeconds": 900, "ExternalId": "ext-123",
        })
        session = assume_role_session(ROLE, "partner", base_session=base, duration_seconds=900,
                                      external_id="ext-123")
        stub.assert_no_pending_responses()
    assert session.get_credentials().access_key == "ASIAFAKEFAKEFAKE0001"
    assert session.region_name == "us-east-1"


def test_refreshable_session_is_lazy_and_caches_valid_credentials():
    sts = stub_client("sts")
    with Stubber(sts) as stub:
        stub.add_response("assume_role", sts_response(1, timedelta(hours=1)))
        session = refreshable_session(ROLE, "lazy", sts_client=sts, region_name="us-east-1")
        assert len(stub._queue) == 1  # nothing called yet
        credentials = session.get_credentials()
        first = credentials.get_frozen_credentials()
        second = credentials.get_frozen_credentials()  # still valid: no second AssumeRole
        stub.assert_no_pending_responses()
    assert first.access_key == second.access_key == "ASIAFAKEFAKEFAKE0001"
    assert first.token == "fake-token-1"


def test_refreshable_session_renews_credentials_close_to_expiry():
    sts = stub_client("sts")
    with Stubber(sts) as stub:
        # Five minutes left is inside botocore's mandatory refresh window.
        stub.add_response("assume_role", sts_response(1, timedelta(minutes=5)))
        stub.add_response("assume_role", sts_response(2, timedelta(hours=1)))
        credentials = refreshable_session(ROLE, "renew", sts_client=sts, region_name="us-east-1").get_credentials()
        assert credentials.get_frozen_credentials().access_key == "ASIAFAKEFAKEFAKE0001"
        assert credentials.get_frozen_credentials().access_key == "ASIAFAKEFAKEFAKE0002"
        stub.assert_no_pending_responses()


def test_refreshable_session_works_end_to_end(aws):
    session = refreshable_session(ROLE, "moto-run")
    arn = session.client("sts").get_caller_identity()["Arn"]
    assert arn == "arn:aws:sts::123456789012:assumed-role/reader/moto-run"


def test_for_each_account_records_failures_per_role(aws):
    roles = ["arn:aws:iam::123456789012:role/reader", "arn:aws:iam::210987654321:role/reader"]

    def account_or_fail(session):
        account = session.client("sts").get_caller_identity()["Account"]
        if account.startswith("2"):
            raise RuntimeError("boom")
        return account

    outcome = for_each_account(roles, account_or_fail, session_name="sweep")
    assert outcome[roles[0]] == "123456789012"
    assert isinstance(outcome[roles[1]], RuntimeError)
