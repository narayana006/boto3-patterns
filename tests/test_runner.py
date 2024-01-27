import importlib

import pytest

from patterns import PATTERNS
from patterns.__main__ import main


def test_listing_names_every_pattern(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    for name in PATTERNS:
        assert f"  {name}" in out


def test_unknown_pattern(capsys):
    assert main(["nope"]) == 2
    assert "unknown pattern: nope" in capsys.readouterr().err


@pytest.mark.parametrize("name", PATTERNS)
def test_every_demo_runs_offline(name, capsys):
    importlib.import_module(f"patterns.{name}").demo()
    assert capsys.readouterr().out.strip()
