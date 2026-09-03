"""Every `lemma …` in this package's own docs must resolve in the Typer app.

`SETUP.md` shipped `lemma files list` (the verb is `ls`) and
`lemma servers select cloud` (the server is `lemma-cloud`), and the setup guide
is the second file a new user reads. Prose cannot be kept true by review; this
walks the real command tree instead, which is the same drift-guard shape as
``test_lazy_groups.py`` and ``test_global_flag_hoisting.py``.

Scope is deliberate: the markdown files in ``lemma-cli/`` only. A guard over
docs this package does not own would fail for someone else's edit.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import click
import pytest
from typer.main import get_group

import lemma_cli.app  # noqa: F401 - registers the `tui` group alongside the core ones
from lemma_cli.cli_core.app import app

DOCS_DIR = Path(__file__).resolve().parent.parent


# Typer 0.26 ships its own click shim (``typer._click``), so its groups and
# options are NOT instances of ``click.Group`` / ``click.Option``. Everything
# below therefore duck-types rather than isinstance-checks.
def _is_group(command: object) -> bool:
    return hasattr(command, "get_command") and hasattr(command, "list_commands")


def _options(command: object) -> dict[str, bool]:
    """option spelling -> whether it consumes the following token."""
    found = {"--help": False, "-h": False}
    for param in getattr(command, "params", []):
        spellings = [
            *getattr(param, "opts", []),
            *(getattr(param, "secondary_opts", None) or []),
        ]
        if not spellings or not all(name.startswith("-") for name in spellings):
            continue  # a positional argument
        for spelling in spellings:
            found[spelling] = not getattr(param, "is_flag", False)
    return found


def _resolve(tokens: list[str]) -> str | None:
    """Return a human-readable problem with ``lemma <tokens>``, or None.

    Two phases, because which options exist depends on which command was named:
    while still inside a group only the *root* globals can appear (they are
    hoisted to the front at parse time anyway), and once a leaf command is
    reached its own options are known exactly.
    """
    root = get_group(app)
    globals_ = _options(root)
    node = root
    ctx = click.Context(root, info_name="lemma")
    path: list[str] = []

    index = 0
    while index < len(tokens) and _is_group(node):
        token = tokens[index]
        if token == "--":
            return None
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            if name not in globals_:
                return (
                    f"`lemma {' '.join([*path, token])}`: {name} is not a global option"
                )
            index += 2 if globals_[name] and "=" not in token else 1
            continue
        sub = node.get_command(ctx, token)
        if sub is None:
            where = " ".join(["lemma", *path]).strip()
            return f"`{where}` has no command {token!r}"
        node = sub
        ctx = click.Context(sub, info_name=token, parent=ctx)
        path.append(token)
        index += 1

    leaf = {**globals_, **_options(node)}
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return None
        if token.startswith("-") and token != "-":
            name = token.split("=", 1)[0]
            if name not in leaf:
                return f"`lemma {' '.join(path)}` has no option {name}"
            index += 2 if leaf[name] and "=" not in token else 1
            continue
        index += 1
    return None


def _documented_commands() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for markdown in sorted(DOCS_DIR.glob("*.md")):
        for number, raw in enumerate(markdown.read_text().splitlines(), start=1):
            line = raw.strip().removeprefix("$ ").strip()
            if not line.startswith("lemma ") and line != "lemma":
                continue
            # Trailing `# comment` explanations are the house style in these files.
            line = line.split("#", 1)[0].strip()
            # Shell plumbing (pipes, redirects, &&) is out of scope: the point is
            # the command name, not the shell line it sits in.
            if any(character in line for character in "|<>&;`$()"):
                continue
            found.append((markdown.name, number, line))
    return found


def test_the_docs_actually_contain_commands():
    assert len(_documented_commands()) > 20


@pytest.mark.parametrize(
    "name,number,line",
    _documented_commands(),
    ids=lambda value: str(value).replace(" ", "_") if isinstance(value, str) else "",
)
def test_documented_command_resolves(name, number, line):
    try:
        tokens = shlex.split(line)[1:]
    except ValueError:  # unbalanced quotes in a prose example
        pytest.skip(f"{name}:{number} is not a parseable shell line")
    problem = _resolve(tokens)
    assert problem is None, (
        f"{name}:{number} documents a command that does not exist — {problem}"
    )
