"""Global options must work in any position, not only before the subcommand.

`lemma tables list --output json` should behave like `lemma --output json tables
list`. LazyRootGroup hoists the root-level global flags to the front so Click's
group parser sees them regardless of where the user put them.
"""

from __future__ import annotations

import typer.main

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.lazy import (
    _GLOBAL_FLAG_OPTS,
    _GLOBAL_VALUE_OPTS,
    hoist_global_options,
)


def test_hoist_moves_output_flag_to_front():
    assert hoist_global_options(["tables", "list", "--output", "json"]) == [
        "--output",
        "json",
        "tables",
        "list",
    ]


def test_hoist_handles_equals_form_and_bare_flags():
    assert hoist_global_options(["tables", "list", "--output=json"]) == [
        "--output=json",
        "tables",
        "list",
    ]
    assert hoist_global_options(["pods", "list", "--json"]) == [
        "--json",
        "pods",
        "list",
    ]


def test_hoist_preserves_subcommand_and_payload_order():
    # A --data JSON payload must not be reordered or split.
    args = [
        "records",
        "create",
        "tickets",
        "--data",
        '{"title":"x"}',
        "--output",
        "json",
    ]
    assert hoist_global_options(args) == [
        "--output",
        "json",
        "records",
        "create",
        "tickets",
        "--data",
        '{"title":"x"}',
    ]


def test_hoist_leaves_already_correct_order_unchanged():
    args = ["--output", "json", "tables", "list"]
    assert hoist_global_options(args) == args


def test_hoist_stops_at_double_dash_separator():
    # Tokens after `--` are positional and must not be hoisted.
    assert hoist_global_options(["run", "--", "--output", "json"]) == [
        "run",
        "--",
        "--output",
        "json",
    ]


def test_every_hoisted_flag_is_a_real_root_option():
    # Catch typos: each hoisted flag must actually be declared on the root callback.
    command = typer.main.get_command(app)
    root_opts: set[str] = set()
    for param in command.params:
        for opt in getattr(param, "opts", []):
            if opt.startswith("--"):
                root_opts.add(opt)
    hoisted = set(_GLOBAL_VALUE_OPTS) | set(_GLOBAL_FLAG_OPTS)
    assert hoisted <= root_opts, f"Hoisted but not a root option: {hoisted - root_opts}"


def test_no_hoisted_flag_collides_with_a_subcommand_option():
    # Safety invariant: a hoisted flag must NEVER also be a subcommand option,
    # otherwise hoisting would steal it from the subcommand (e.g. `servers add
    # --base-url`). This guard scans the command modules for declared options.
    import pathlib
    import re

    cmd_dir = (
        pathlib.Path(__file__).resolve().parents[1]
        / "lemma_cli"
        / "cli_core"
        / "commands"
    )
    subcommand_opts: set[str] = set()
    for f in cmd_dir.glob("*.py"):
        subcommand_opts.update(re.findall(r'"(--[a-z][a-z-]+)"', f.read_text()))
    hoisted = set(_GLOBAL_VALUE_OPTS) | set(_GLOBAL_FLAG_OPTS)
    collisions = hoisted & subcommand_opts
    assert not collisions, (
        f"These hoisted flags are also subcommand options and must not be hoisted: "
        f"{collisions}"
    )
