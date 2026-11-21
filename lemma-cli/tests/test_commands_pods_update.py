"""`lemma pods update`: rename a Lemma, or change only the fields named."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands.pods import update_payload

runner = CliRunner()


def test_a_rename_sends_only_the_name():
    assert update_payload({}, name="Care") == {"name": "Care"}


def test_flags_win_over_a_json_payload():
    sent = update_payload(
        {"name": "From data", "description": "kept"}, name="From flag"
    )
    assert sent == {"name": "From flag", "description": "kept"}


def test_nothing_to_change_is_a_usage_error():
    with pytest.raises(typer.BadParameter):
        update_payload({})


def test_the_command_refuses_an_empty_update_before_any_call():
    result = runner.invoke(app, ["pods", "update", "Support"])
    assert result.exit_code == 2
