"""Lazy command-group registry stays in sync with the real sub-apps.

The root CLI lists command groups from static metadata (lemma_cli/cli_core/
lazy.py) so that `lemma --help` and single-group commands never import the
whole command tree. These tests load every registered group for real and
assert the static name/help/hidden metadata matches what eager registration
would have shown.
"""

from __future__ import annotations

import importlib

import typer.main

from lemma_cli.app import LAZY_GROUPS, app
from typer.testing import CliRunner

runner = CliRunner()


def test_every_lazy_group_resolves_and_help_matches():
    for name, (module, attr, short_help, _hidden) in LAZY_GROUPS.items():
        sub_app = getattr(importlib.import_module(module), attr)
        group = typer.main.get_group(sub_app)
        real_help = (group.help or "").strip().splitlines()
        first_line = real_help[0] if real_help else ""
        assert first_line == short_help, (
            f"lazy registry help for {name!r} is {short_help!r} but the real "
            f"group says {first_line!r} — update LAZY_GROUPS in cli_core/app.py"
        )


def test_lazy_group_dispatch_shows_subcommand_help():
    result = runner.invoke(app, ["pods", "--help"])
    assert result.exit_code == 0, result.output
    assert "list" in result.output


def test_root_help_lists_lazy_groups_without_loading_them(monkeypatch):
    import sys

    # Drop the surfaces module so we can assert `--help` does not re-import it.
    # Use monkeypatch.delitem (not a bare `del`) so the original module object is
    # restored on teardown — otherwise a later re-import installs a *fresh*
    # surfaces module, and other tests holding an import-time reference (e.g.
    # test_cli_v2's `monkeypatch.setattr(surfaces, "run_with_client", ...)`) would
    # patch a stale object and silently fall through to the real network.
    for mod in list(sys.modules):
        if mod.startswith("lemma_cli.cli_core.commands.surfaces"):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "surfaces" in result.output
    assert "lemma_cli.cli_core.commands.surfaces" not in sys.modules


def test_hidden_aliases_stay_hidden():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "organizations " not in result.output
