"""`CONVENTIONS.md` says a `list` supports `--limit`; this walks the real app.

The flag is only worth teaching if it is universal. The skills teach it as one
(`lemma records list tickets --limit 20`), so an agent that reaches for it on a
command that lacks it gets a parse error (exit 2) rather than a result, and
learns nothing about which commands are different.

The exemptions below are commands that list a *complete* set — there is no
second page to ask for, so `--limit` could only truncate an answer that is
already whole. Anything else that turns up here needs the flag, not an entry.
"""

from __future__ import annotations

import importlib

import pytest
import typer.main

from lemma_cli.app import LAZY_GROUPS

pytestmark = pytest.mark.unit

#: command path -> why it lists a complete set rather than a page.
COMPLETE_SET_LISTINGS = {
    "servers list": "the servers in ~/.lemma/config.json — a local file, read whole",
    "skill list": "the skills vendored into this wheel — a fixed set",
    "skills list": "the skills vendored into this wheel — a fixed set",
    "runtime profiles list": (
        "the org's runtime profiles — the endpoint takes neither limit nor "
        "page_token and answers with all of them"
    ),
}


def _commands() -> dict[str, set[str]]:
    """Every leaf command in the app, mapped to the option strings it declares.

    `LAZY_GROUPS` comes from `lemma_cli.app`, the console-script entry point,
    rather than from `cli_core.app` underneath it. The two share one dict and
    the entry point `setdefault`s the `tui` group into it, so importing the
    lower module walks 352 commands alone and 353 when an earlier test
    imported the entry point first — what this guard checked depended on test
    ordering.

    Groups are found by duck typing rather than `isinstance(cmd, click.Group)`:
    typer vendors its own click, so a `TyperGroup` is not an instance of the
    top-level `click.Group` and a nested sub-app reads as a leaf.
    """
    found: dict[str, set[str]] = {}

    def walk(command: object, path: list[str]) -> None:
        children = getattr(command, "commands", None)
        if children:
            for name, child in children.items():
                walk(child, [*path, name])
            return
        found[" ".join(path)] = {
            opt for param in command.params for opt in (param.opts or [])
        }

    for group_name, (module, attr, *_rest) in LAZY_GROUPS.items():
        sub_app = getattr(importlib.import_module(module), attr)
        walk(typer.main.get_command(sub_app), [group_name])
    return found


def test_every_list_command_takes_limit_or_is_a_declared_complete_set():
    missing = sorted(
        name
        for name, opts in _commands().items()
        if name.split()[-1] in {"list", "ls"}
        and "--limit" not in opts
        and name not in COMPLETE_SET_LISTINGS
    )

    assert missing == [], (
        f"{missing} are list-shaped but do not declare --limit. Add the flag, "
        "or — only if the command returns a complete set with no second page — "
        "add it to COMPLETE_SET_LISTINGS with the reason."
    )


def test_the_exemption_list_has_not_outlived_its_commands():
    """An exemption for a command that no longer exists is how the guard rots
    into a list of names nobody checks."""
    commands = _commands()

    stale = sorted(name for name in COMPLETE_SET_LISTINGS if name not in commands)

    assert stale == []


def test_the_exemptions_still_lack_the_flag():
    """The other direction: once a command grows `--limit`, its exemption is a
    stale claim about the API that the next reader will believe."""
    commands = _commands()

    now_limited = sorted(
        name for name in COMPLETE_SET_LISTINGS if "--limit" in commands.get(name, set())
    )

    assert now_limited == []
