from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import machine
from lemma_cli.cli_core.commands.machine import split_machine_path


class FakeStream:
    def __init__(
        self, chunks: list[tuple[str, str]], exit_code: int | None, state: str
    ):
        self._chunks = chunks
        self.exit_code: int | None = None
        self.state: str | None = None
        self._final = (exit_code, state)
        self.process_id = "p-1"
        self.killed = False

    def __iter__(self):
        for channel, data in self._chunks:
            yield SimpleNamespace(channel=channel, data=data, sequence=0)
        self.exit_code, self.state = self._final

    def kill(self) -> None:
        self.killed = True


class FakeFiles:
    def __init__(self) -> None:
        self.written: dict[tuple[str, str], bytes] = {}
        self.remote: dict[tuple[str, str], bytes] = {}

    def write(self, ref: str, path: str, handle: Any) -> dict[str, Any]:
        self.written[(ref, path)] = handle.read()
        return {"path": path, "size_bytes": len(self.written[(ref, path)])}

    def download_to(self, ref: str, path: str, target: Path) -> Path:
        Path(target).write_bytes(self.remote[(ref, path)])
        return Path(target)


class FakeProcesses:
    def __init__(self, calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]]):
        self._calls = calls

    def list(self, ref: str = "me") -> dict[str, Any]:
        self._calls.append(("ps", (ref,), {}))
        return {"processes": [{"id": "p-1", "ref": ref}]}

    def kill(self, ref: str, pid: str) -> None:
        self._calls.append(("kill", (ref, pid), {}))


class FakeMachines:
    def __init__(self, stream: FakeStream | None = None) -> None:
        self.stream = stream
        self.files = FakeFiles()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.processes = FakeProcesses(self.calls)

    def exec(self, ref: str, line: str, **kwargs: Any) -> FakeStream:
        self.calls.append(("exec", (ref, line), kwargs))
        assert self.stream is not None
        return self.stream

    def reset(self, ref: str, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("reset", (ref, scopes), kwargs))
        return {"results": [{"scope": s, "ok": True} for s in scopes]}


#: The pod `--pod` names in these tests, and the stored default it must beat.
POD_A = "11111111-1111-1111-1111-111111111111"
POD_B = "22222222-2222-2222-2222-222222222222"


def _state_with_pod(pod_id: str, output: str = "pretty") -> SimpleNamespace:
    """A CLI state whose stored default pod is ``pod_id`` (a UUID, as stored
    defaults are)."""
    return SimpleNamespace(
        config={"defaults": {"org_id": "org-1", "pod_id": pod_id}},
        output=output,
        full=False,
    )


def _patch(patch_run, state, machines: FakeMachines) -> None:
    patch_run(machine, client=SimpleNamespace(machines=machines), state=state)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("./a.txt", None),
        ("/tmp/a.txt", None),
        ("me:/home/user/lemma/a", ("me", "/home/user/lemma/a")),
        ("me:notes.txt", ("me", "notes.txt")),
        ("mine:mac:/Users/me/a", ("mine:mac", "/Users/me/a")),
        ("pod:abc/builder:/home/user/x", ("pod:abc/builder", "/home/user/x")),
        ("pod:abc:builder:/home/user/x", ("pod:abc:builder", "/home/user/x")),
        ("pod:builder:dist/app.zip", ("pod:builder", "dist/app.zip")),
        # A path may hold colons too: the ref ends where its grammar does.
        ("me:/home/user/a:b", ("me", "/home/user/a:b")),
        ("mine:mac:/a:b", ("mine:mac", "/a:b")),
        ("mine:mac:notes:v2.txt", ("mine:mac", "notes:v2.txt")),
        ("pod:p:n:/path", ("pod:p:n", "/path")),
        ("pod:p:n:/a:b", ("pod:p:n", "/a:b")),
        ("pod:n:/a:b", ("pod:n", "/a:b")),
        ("pod:abc/builder:a:b", ("pod:abc/builder", "a:b")),
        ("pod:Team Pod:builder:x.txt", ("pod:Team Pod:builder", "x.txt")),
        ("pod:builder:My File:1.txt", ("pod:builder", "My File:1.txt")),
        (
            "11111111-1111-1111-1111-111111111111:/home/user/a",
            ("11111111-1111-1111-1111-111111111111", "/home/user/a"),
        ),
    ],
)
def test_machine_paths_split_into_ref_and_path(spec, expected) -> None:
    assert split_machine_path(spec) == expected


@pytest.mark.parametrize("spec", ["pod:builder", "mine:mac"])
def test_a_ref_with_no_path_is_a_usage_error(spec) -> None:
    with pytest.raises(typer.BadParameter):
        split_machine_path(spec)


def test_exec_streams_output_and_exits_with_the_commands_code(
    runner, patch_run
) -> None:
    stream = FakeStream([("stdout", "hello\n"), ("stderr", "oops\n")], 3, "failed")
    machines = FakeMachines(stream)
    _patch(patch_run, _state_with_pod(POD_B), machines)

    result = runner.invoke(
        app, ["machine", "exec", "--env", "A=1", "pod:builder", "ls", "-la"]
    )

    assert result.exit_code == 3
    assert result.stdout == "hello\n"
    assert result.stderr == "oops\n"
    assert machines.calls == [
        (
            "exec",
            (f"pod:{POD_B}:builder", "ls -la"),
            {"cwd": None, "env": {"A": "1"}, "timeout_seconds": None},
        )
    ]


def test_exec_runs_a_single_argument_as_a_shell_line(
    runner, patch_run, json_state
) -> None:
    machines = FakeMachines(FakeStream([("stdout", "ok\n")], 0, "succeeded"))
    _patch(patch_run, json_state, machines)

    result = runner.invoke(
        app, ["machines", "exec", "--json", "me", "npm ci && npm test"]
    )

    assert result.exit_code == 0, result.stderr
    assert machines.calls[0][1] == ("me", "npm ci && npm test")
    assert json.loads(result.stdout) == {
        "process_id": "p-1",
        "state": "succeeded",
        "exit_code": 0,
        "stdout": "ok\n",
        "stderr": "",
    }


def test_exec_with_pod_flag_targets_that_pod_not_the_stored_default(
    runner, patch_run
) -> None:
    machines = FakeMachines(FakeStream([], 0, "succeeded"))
    _patch(patch_run, _state_with_pod(POD_B), machines)

    result = runner.invoke(
        app, ["machine", "exec", "--pod", POD_A, "pod:bay-1", "true"]
    )

    assert result.exit_code == 0, result.stderr
    ((verb, (ref, _line), _kwargs),) = machines.calls
    assert verb == "exec"
    assert ref == f"pod:{POD_A}:bay-1"
    assert POD_B not in ref


@pytest.mark.parametrize("flag", [["--pod", POD_A], [f"--pod={POD_A}"]])
def test_a_pod_flag_after_the_ref_is_a_usage_error(runner, patch_run, flag) -> None:
    """Everything after the ref is the command, so a `--pod` typed there is
    swallowed into the shell line and the ref binds to the stored default
    pod. Refuse it rather than run on the wrong pod."""
    machines = FakeMachines(FakeStream([], 0, "succeeded"))
    _patch(patch_run, _state_with_pod(POD_B), machines)

    result = runner.invoke(app, ["machine", "exec", "pod:bay-1", "true", *flag])

    assert result.exit_code == 2
    assert "--pod" in result.output
    assert machines.calls == []  # nothing ran, on any pod


def test_a_bare_pod_ref_resolves_to_the_stored_default(runner, patch_run) -> None:
    machines = FakeMachines(FakeStream([], 0, "succeeded"))
    _patch(patch_run, _state_with_pod(POD_B), machines)

    result = runner.invoke(app, ["machine", "exec", "pod:bay-1", "true"])

    assert result.exit_code == 0, result.stderr
    assert machines.calls[0][1][0] == f"pod:{POD_B}:bay-1"


def test_the_refs_own_pod_wins_over_the_flag(runner, patch_run) -> None:
    named = "33333333-3333-3333-3333-333333333333"
    machines = FakeMachines(FakeStream([], 0, "succeeded"))
    _patch(patch_run, _state_with_pod(POD_B), machines)

    result = runner.invoke(
        app, ["machine", "exec", "--pod", POD_A, f"pod:{named}:bay-1", "true"]
    )

    assert result.exit_code == 0, result.stderr
    assert machines.calls[0][1][0] == f"pod:{named}:bay-1"


def test_a_quoted_command_may_itself_mention_pod(runner, patch_run) -> None:
    """One argument is a shell line as written; a `--pod` inside it is the
    command's own, not the CLI's."""
    machines = FakeMachines(FakeStream([], 0, "succeeded"))
    _patch(patch_run, _state_with_pod(POD_B), machines)

    result = runner.invoke(
        app, ["machine", "exec", "pod:bay-1", "kubectl logs --pod web"]
    )

    assert result.exit_code == 0, result.stderr
    assert machines.calls[0][1] == (
        f"pod:{POD_B}:bay-1",
        "kubectl logs --pod web",
    )


@pytest.mark.parametrize(
    "args",
    [
        ["machine", "exec", "--pod", POD_A, "me", "true"],
        ["machine", "ps", "--pod", POD_A, "me"],
        ["machine", "kill", "--pod", POD_A, "me", "p-1", "-y"],
    ],
)
def test_the_pod_flag_needs_a_pod_ref(runner, patch_run, args) -> None:
    machines = FakeMachines(FakeStream([], 0, "succeeded"))
    _patch(patch_run, _state_with_pod(POD_B), machines)

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert machines.calls == []


def test_ps_and_kill_resolve_the_ref_like_exec(runner, patch_run) -> None:
    machines = FakeMachines()
    _patch(patch_run, _state_with_pod(POD_B), machines)

    listed = runner.invoke(app, ["machine", "ps", "--pod", POD_A, "pod:bay-1"])
    killed = runner.invoke(
        app, ["machine", "kill", "--pod", POD_A, "pod:bay-1", "p-1", "-y"]
    )

    assert (listed.exit_code, killed.exit_code) == (0, 0)
    assert machines.calls == [
        ("ps", (f"pod:{POD_A}:bay-1",), {}),
        ("kill", (f"pod:{POD_A}:bay-1", "p-1"), {}),
    ]


def test_shell_resolves_the_ref_like_exec(runner, patch_run, monkeypatch) -> None:
    machines = FakeMachines()
    _patch(patch_run, _state_with_pod(POD_B), machines)
    seen: list[str] = []

    from lemma_cli.cli_core import machine_shell

    def fake_run_shell(client: Any, ref: str, command: str) -> int:
        seen.append(ref)
        return 0

    monkeypatch.setattr(machine_shell, "run_shell", fake_run_shell)
    # CliRunner replaces sys.stdin/stdout with pipes, so look like a terminal
    # to the guard; the patched run_shell never touches a real one.
    monkeypatch.setattr(
        machine,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True),
        ),
    )

    result = runner.invoke(app, ["machine", "shell", "--pod", POD_A, "pod:bay-1"])

    assert result.exit_code == 0, result.stderr
    assert seen == [f"pod:{POD_A}:bay-1"]


@pytest.mark.parametrize("command", ["exec", "shell", "ps", "kill"])
def test_machine_commands_advertise_the_pod_flag(runner, command) -> None:
    result = runner.invoke(app, ["machine", command, "--help"])

    assert result.exit_code == 0
    assert "--pod" in result.output


def test_cp_uploads_a_local_file_into_a_directory(
    runner, patch_run, json_state, tmp_path
) -> None:
    local = tmp_path / "report.csv"
    local.write_bytes(b"a,b\n")
    machines = FakeMachines()
    _patch(patch_run, json_state, machines)

    result = runner.invoke(app, ["machine", "cp", str(local), "me:/home/user/lemma/"])

    assert result.exit_code == 0, result.stderr
    assert machines.files.written == {("me", "/home/user/lemma/report.csv"): b"a,b\n"}


def test_cp_downloads_a_machine_file(runner, patch_run, json_state, tmp_path) -> None:
    machines = FakeMachines()
    machines.files.remote[("pod:builder", "/home/user/lemma/app.zip")] = b"PK"
    _patch(patch_run, json_state, machines)

    result = runner.invoke(
        app,
        ["machine", "cp", "pod:builder:/home/user/lemma/app.zip", str(tmp_path) + "/"],
    )

    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "app.zip").read_bytes() == b"PK"


def test_cp_needs_exactly_one_side_on_a_machine(runner, patch_run, json_state) -> None:
    _patch(patch_run, json_state, FakeMachines())

    result = runner.invoke(app, ["machine", "cp", "./a", "./b"])

    assert result.exit_code == 1
    assert "<ref>:<path>" in result.stderr


def test_reset_of_work_losing_scopes_needs_confirm(
    runner, patch_run, json_state
) -> None:
    machines = FakeMachines()
    _patch(patch_run, json_state, machines)

    refused = runner.invoke(app, ["machine", "reset", "me", "--scope", "home", "-y"])
    assert refused.exit_code == 1
    assert "--confirm" in refused.stderr
    assert machines.calls == []

    accepted = runner.invoke(
        app,
        [
            "machine",
            "reset",
            "me",
            "-s",
            "home",
            "-s",
            "caches",
            "--confirm",
            "--forget-logins",
            "-y",
        ],
    )
    assert accepted.exit_code == 0, accepted.stderr
    assert machines.calls == [
        ("reset", ("me", ["home", "caches"]), {"confirm": True, "forget_logins": True})
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["machine", "kill", "me", "p-1"],
        ["machine", "reset", "me", "--scope", "caches"],
        ["machine", "preview", "revoke", "me", "11111111-1111-1111-1111-111111111111"],
        ["machine", "logins", "forget", "me", "example.com"],
    ],
)
def test_destructive_machine_commands_require_yes(runner, args) -> None:
    """Refused before any client exists: no server is configured here, so a
    command that went on to call one would fail with a different message."""
    refused = runner.invoke(app, args)

    assert refused.exit_code == 1
    assert "--yes" in refused.stderr


def test_shell_refuses_without_a_terminal(runner) -> None:
    result = runner.invoke(app, ["machine", "shell", "me"])

    assert result.exit_code == 1
    assert "lemma machine exec me" in " ".join(result.stderr.split())
