"""Lazy subcommand loading for the Lemma CLI.

The CLI registers ~20 command groups, each of which imports its slice of the
SDK (generated request models, attrs classes, helpers). Loading all of them on
every invocation dominated startup — `lemma --help` paid for the full tree,
and `lemma pods list` paid for agents, surfaces, connectors, and the rest.
This matters most in the workspace sandbox, where gVisor and a 500m CPU limit
multiply every import.

`LazyRootGroup` lists lazy groups from a static registry (name, module, attr,
help text) without importing anything. `LazyGroupProxy` stands in for a group
in listings using that static help text; the first time the group is actually
used — dispatched into, asked for its own --help, or shell-completed — the
proxy imports the module and delegates everything to the real Typer group.

The registry's help strings are pinned to the real sub-app help by
tests/test_lazy_groups.py, so they cannot drift silently.
"""
from __future__ import annotations

import importlib

import typer
import typer.core

# name -> (module path, Typer-app attribute, one-line help, hidden)
LazyEntry = tuple[str, str, str, bool]


# Root-only global options (see cli_core/app.py:root). Click only parses
# group-level options that appear *before* the subcommand name, so
# `lemma tables list --output json` normally errors. We hoist these to the front
# (see LazyRootGroup.parse_args) so they work in any position — the CLI behaves
# intuitively instead of forcing a "put it before the subcommand" rule.
#
# IMPORTANT: only options that are NEVER also subcommand options may be hoisted.
# `--base-url`, `--auth-url`, `--token`, `--server`, `--org`, and `--pod` are
# deliberately excluded: subcommands like `servers add`, `runtime profiles
# create`, and the per-pod commands declare their own `--base-url`/`--pod`/etc.,
# where the flag means something local. Hoisting those would steal them from the
# subcommand. (`--pod`/`--org` already work in both positions on the commands
# that declare them, so they don't need hoisting.) The drift guard in
# tests/test_global_flag_hoisting.py enforces this invariant.
_GLOBAL_VALUE_OPTS = frozenset(
    {
        "--conversation-id",
        "--timeout",
        "--config-file",
        "--output",
    }
)
_GLOBAL_FLAG_OPTS = frozenset({"--json", "--no-verify-ssl", "--full"})


def hoist_global_options(args: list[str]) -> list[str]:
    """Move recognized root-level global options to the front of ``args``.

    Value options in space form (``--output json``) carry their value token along;
    ``--opt=value`` form is a single token. Everything after a ``--`` separator is
    left untouched. Non-global tokens (the subcommand and its own options/values)
    keep their original order, so only the global flags move.
    """
    head: list[str] = []
    rest: list[str] = []
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        if tok == "--":
            rest.extend(args[i:])
            break
        name = tok.split("=", 1)[0]
        if name in _GLOBAL_VALUE_OPTS:
            head.append(tok)
            if "=" not in tok and i + 1 < n:
                head.append(args[i + 1])
                i += 2
            else:
                i += 1
        elif name in _GLOBAL_FLAG_OPTS:
            head.append(tok)
            i += 1
        else:
            rest.append(tok)
            i += 1
    return head + rest


class LazyGroupProxy(typer.core.TyperGroup):
    """Stand-in for a sub-Typer group; imports its module on first real use."""

    def __init__(self, *, name: str, module: str, attr: str, short_help: str, hidden: bool = False) -> None:
        super().__init__(name=name, short_help=short_help, help=short_help, hidden=hidden)
        self._module = module
        self._attr = attr
        self._real_group: typer.core.TyperGroup | None = None

    def _real(self) -> typer.core.TyperGroup:
        if self._real_group is None:
            sub_app = getattr(importlib.import_module(self._module), self._attr)
            group = typer.main.get_group(sub_app)
            group.name = self.name
            self._real_group = group
        return self._real_group

    # Everything below delegates to the real group so parsing, dispatch, help,
    # and completion behave exactly as if the group had been registered
    # eagerly with app.add_typer().

    def make_context(self, info_name, args, parent=None, **extra):  # type: ignore[no-untyped-def]
        return self._real().make_context(info_name, args, parent=parent, **extra)

    def invoke(self, ctx):  # type: ignore[no-untyped-def]
        return self._real().invoke(ctx)

    def get_command(self, ctx, name):  # type: ignore[no-untyped-def]
        return self._real().get_command(ctx, name)

    def list_commands(self, ctx):  # type: ignore[no-untyped-def]
        return self._real().list_commands(ctx)

    def get_help(self, ctx):  # type: ignore[no-untyped-def]
        return self._real().get_help(ctx)

    def format_help(self, ctx, formatter):  # type: ignore[no-untyped-def]
        return self._real().format_help(ctx, formatter)

    def shell_complete(self, ctx, incomplete):  # type: ignore[no-untyped-def]
        return self._real().shell_complete(ctx, incomplete)


class LazyRootGroup(typer.core.TyperGroup):
    """Root group that resolves subcommand groups from a lazy registry.

    Pass via ``typer.Typer(cls=LazyRootGroup)`` and populate ``registry``
    (insertion order is listing order, after eagerly registered commands).
    """

    registry: dict[str, LazyEntry] = {}

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._lazy_cache: dict[str, LazyGroupProxy] = {}

    def parse_args(self, ctx, args):  # type: ignore[no-untyped-def]
        # Let global flags appear anywhere: hoist them before the subcommand so
        # Click's group parser sees them. Without this, `lemma tables list
        # --output json` errors because `--output` is only in scope before the
        # subcommand name.
        return super().parse_args(ctx, hoist_global_options(args))

    def _lazy(self, name: str) -> LazyGroupProxy:
        proxy = self._lazy_cache.get(name)
        if proxy is None:
            module, attr, short_help, hidden = self.registry[name]
            proxy = LazyGroupProxy(
                name=name, module=module, attr=attr, short_help=short_help, hidden=hidden
            )
            self._lazy_cache[name] = proxy
        return proxy

    def get_command(self, ctx, name):  # type: ignore[no-untyped-def]
        if name in self.registry:
            return self._lazy(name)
        return super().get_command(ctx, name)

    def list_commands(self, ctx):  # type: ignore[no-untyped-def]
        return [*super().list_commands(ctx), *self.registry]
