"""`--output json` promises a parseable stream; this walks the real app.

`telemetry.py` states the discipline — "stderr, never stdout: `--output json`
promises a parseable stream" — and `fail()` funnels every runtime error to
`err_console` for exactly that reason. What no test covered was the *success*
path: a command that renders its result with `console.print` instead of
`emit()` puts prose on stdout in JSON mode, and nothing noticed.

Two commands were doing it when this test was written, and they failed in
different ways:

* the seven `<resource> schema` commands (fourteen, counting the singular and
  plural aliases) printed a JSONC template — JSON *with comments*, which is the
  point of a scaffold and is not parseable. `| jq` got a syntax error.
* `pod doctor` printed its findings as prose and never emitted the report at
  all. Not a diagnostic leaking into stdout: `--output json` returned no
  machine-readable answer whatsoever, having already built one.

**What this covers, honestly.** Commands are invoked against a permissive stub
client, so the ones reachable without real input are the ones checked — 91 to
93 of 353 leaf commands, the exact number depending on what else ran first. The rest exit 2 on a usage error
because they need a file, a JSON body, or a name that cannot be invented, and
that is correct behaviour rather than a gap this test should paper over. So
this asserts the contract for every command it can *reach*, and separately
asserts the reach itself has not collapsed — a change that made every command
exit 2 would otherwise turn this green while checking nothing.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import pkgutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer.main
from typer.testing import CliRunner

import lemma_cli.cli_core.commands as commands_pkg
from lemma_cli.app import LAZY_GROUPS, app

pytestmark = pytest.mark.unit

UUID = "11111111-1111-1111-1111-111111111111"

#: A floor against collapse, deliberately below the observed range rather than
#: equal to it: the walk reaches 93 commands when this file runs alone and 91
#: in the full suite, so something earlier in a session shifts two of them.
#: The number's job is to catch the walk reaching *nothing* — a change that
#: made every command exit early would otherwise leave the contract test above
#: passing while asserting on an empty set. It is not a claim about the exact
#: count, and raising it to the observed maximum only makes the suite fail.
MINIMUM_COMMANDS_REACHED = 85

#: Seconds any one command may take before this test calls it a hang. Generous:
#: the whole walk runs in about four seconds, so this only fires on a command
#: that never returns.
COMMAND_DEADLINE_SECONDS = 20.0

#: command path -> why invoking it here would do something other than print.
#: These do not go through `run_with_client`, so stubbing it does not reach
#: them; they open a browser, take over the terminal, or talk to the auth
#: server directly. `auth login` is the reason this list exists: walking it
#: fetched `/auth/cli/info` from `DEFAULT_BASE_URL` -- production -- opened a
#: real browser, and polled for the full 300s login wait.
NOT_INVOCABLE = {
    "auth login": "browser login: fetches /auth/cli/info, opens a browser, polls",
    "auth logout": "revokes the session it finds, which under HOME isolation is nobody's",
    "tui": "the full-screen terminal UI; it takes the terminal and does not return",
}


def _commands() -> dict[str, object]:
    """Every leaf command in the app.

    These come from `lemma_cli.app`, the console-script entry point, rather
    than from `cli_core.app` underneath it. The two share one `LAZY_GROUPS`
    dict and the entry point `setdefault`s the `tui` group into it, so
    importing the lower module walks 352 commands when this file runs alone
    and 353 when some earlier test happened to import the entry point first.
    The extra one is `tui`, which takes the terminal and never returns: the
    suite hung on exactly that, with no indication of which command did it.

    Groups are found by duck typing rather than `isinstance(cmd, click.Group)`:
    typer vendors its own click, so a `TyperGroup` is not an instance of the
    top-level `click.Group` and a nested sub-app reads as a leaf. (That same
    vendoring is why `print_resource_schema` takes a `ctx` instead of calling
    `click.get_current_context()`, which answers `None` from here.)
    """
    found: dict[str, object] = {}

    def walk(command: object, path: list[str]) -> None:
        children = getattr(command, "commands", None)
        if children:
            for name, child in children.items():
                walk(child, [*path, name])
            return
        found[" ".join(path)] = command

    for group_name, (module, attr, *_rest) in LAZY_GROUPS.items():
        sub_app = getattr(importlib.import_module(module), attr)
        walk(typer.main.get_command(sub_app), [group_name])
    return found


class _Stub:
    """Stands in for the SDK client: any attribute or call, and dict-shaped.

    `to_dict` and `get` are answered concretely because `io.to_plain` and
    `io.list_items` duck-type on them — a stub that returned itself from
    `to_dict()` would recurse forever instead of rendering.
    """

    def __getattr__(self, name: str) -> object:
        if name == "to_dict":
            return lambda: {"items": [], "id": UUID, "name": "x"}
        if name == "get":
            return lambda key, default=None: default
        if name in {"value", "_asdict"}:
            raise AttributeError(name)
        return _Stub()

    def __call__(self, *args: object, **kwargs: object) -> "_Stub":
        return _Stub()

    def __len__(self) -> int:
        return 0

    def __iter__(self):
        return iter(())

    def __bool__(self) -> bool:
        return True

    def __str__(self) -> str:
        return "x"


def _json_state() -> SimpleNamespace:
    return SimpleNamespace(
        config={
            "defaults": {"org_id": "org-1", "pod_id": UUID},
            "_runtime": {"pod": UUID},
            "servers": {},
        },
        output="json",
        full=False,
    )


def _command_modules() -> list[object]:
    root = Path(commands_pkg.__file__).parent
    return [
        importlib.import_module(f"lemma_cli.cli_core.commands.{info.name}")
        for info in pkgutil.iter_modules([str(root)])
    ]


def _invoke_within_deadline(runner: CliRunner, argv: list[str]):
    """Run one command, or return `None` if it does not finish in time.

    A daemon thread rather than `signal.alarm`: this suite also runs on
    Windows, where there is no SIGALRM. A command that never returns leaks its
    thread, which is why the caller reports it and the fix is to name it in
    `NOT_INVOCABLE` rather than to raise this deadline.

    Without this, an interactive command turns the whole suite into a silent
    hang with no indication of which command did it -- `tui` did exactly that,
    and finding it took a traced run.
    """
    box: list[object] = []

    def call() -> None:
        box.append(runner.invoke(app, argv, catch_exceptions=True))

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(COMMAND_DEADLINE_SECONDS)
    return box[0] if box else None


def _run_every_command() -> tuple[list[tuple[str, str]], list[str], int]:
    """Invoke each leaf command under `--output json`.

    Returns the ones that wrote non-JSON to stdout, the ones that never
    returned, and how many were reached.
    """
    runner = CliRunner()
    modules = _command_modules()
    # An isolated cwd, because this *runs* the commands: `pod init` and its
    # siblings scaffold a bundle into the working directory, and walking them
    # from the checkout left a stray `lemma-cli/x/` tree behind -- named after
    # the stub, which is how it was noticed.
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
        return _walk(runner, modules)


def _walk(runner: CliRunner, modules: list[object]):
    def stub_run(ctx: object, fn) -> object:
        return fn(_Stub(), _json_state())

    offenders: list[tuple[str, str]] = []
    hung: list[str] = []
    reached = 0
    for name, command in sorted(_commands().items()):
        if name in NOT_INVOCABLE:
            continue
        argv = name.split() + ["--output", "json"]
        for param in command.params:
            if not param.required:
                continue
            if param.__class__.__name__ == "Argument":
                argv.append(UUID)
            elif param.opts:
                argv += [param.opts[0], UUID]

        with contextlib.ExitStack() as stack:
            for module in modules:
                if hasattr(module, "run_with_client"):
                    stack.enter_context(_patched(module, stub_run))
            result = _invoke_within_deadline(runner, argv)

        if result is None:
            hung.append(name)
            continue
        if result.exit_code != 0:
            continue
        reached += 1
        stdout = result.stdout.strip()
        if not stdout:
            continue
        try:
            json.loads(stdout)
        except ValueError:
            offenders.append((name, stdout.splitlines()[0][:70]))
    return offenders, hung, reached


@contextlib.contextmanager
def _patched(module: object, replacement) -> object:
    original = module.run_with_client
    module.run_with_client = replacement
    try:
        yield
    finally:
        module.run_with_client = original


def test_no_command_writes_non_json_to_stdout_in_json_mode():
    offenders, _hung, _reached = _run_every_command()
    rendered = "\n".join(f"  {name}: {first!r}" for name, first in offenders)
    assert offenders == [], (
        "these commands wrote something other than JSON to stdout under "
        f"--output json:\n{rendered}\n"
        "Render the result through `emit(state, ...)`, and send progress, "
        "warnings and advisories to `err_console` instead of `console`."
    )


def test_the_walk_still_reaches_the_commands_it_claims_to():
    """A gate nothing reaches is a gate that passes for the wrong reason."""
    _offenders, hung, reached = _run_every_command()
    assert hung == [], (
        f"{hung} did not return within {COMMAND_DEADLINE_SECONDS}s. A command "
        "that takes the terminal or polls forever cannot be walked here — add "
        "it to NOT_INVOCABLE with the reason."
    )
    assert reached >= MINIMUM_COMMANDS_REACHED, (
        f"only {reached} commands ran to completion under the stub, down from "
        f"{MINIMUM_COMMANDS_REACHED}. Something made commands exit early, so "
        "the JSON contract above is being asserted against fewer of them than "
        "before — find out what, rather than lowering this number."
    )


def test_the_not_invocable_list_has_not_outlived_its_commands():
    """An exemption for a command that no longer exists is how a guard rots
    into a list of names nobody checks."""
    stale = sorted(set(NOT_INVOCABLE) - set(_commands()))
    assert stale == [], (
        f"{stale} are exempted but no longer exist. Drop them from NOT_INVOCABLE."
    )
