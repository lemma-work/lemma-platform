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


class FakeMachines:
    def __init__(self, stream: FakeStream | None = None) -> None:
        self.stream = stream
        self.files = FakeFiles()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.processes = SimpleNamespace(
            list=lambda ref: {"processes": [{"id": "p-1", "ref": ref}]},
            kill=lambda ref, pid: self.calls.append(("kill", (ref, pid), {})),
        )

    def exec(self, ref: str, line: str, **kwargs: Any) -> FakeStream:
        self.calls.append(("exec", (ref, line), kwargs))
        assert self.stream is not None
        return self.stream

    def reset(self, ref: str, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("reset", (ref, scopes), kwargs))
        return {"results": [{"scope": s, "ok": True} for s in scopes]}


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
        (
            "11111111-1111-1111-1111-111111111111:/home/user/a",
            ("11111111-1111-1111-1111-111111111111", "/home/user/a"),
        ),
    ],
)
def test_machine_paths_split_into_ref_and_path(spec, expected) -> None:
    assert split_machine_path(spec) == expected


def test_a_ref_with_no_path_is_a_usage_error() -> None:
    with pytest.raises(typer.BadParameter):
        split_machine_path("pod:builder")


def test_exec_streams_output_and_exits_with_the_commands_code(
    runner, patch_run, pod_state
) -> None:
    stream = FakeStream([("stdout", "hello\n"), ("stderr", "oops\n")], 3, "failed")
    machines = FakeMachines(stream)
    _patch(patch_run, pod_state, machines)

    result = runner.invoke(
        app, ["machine", "exec", "--env", "A=1", "pod:builder", "ls", "-la"]
    )

    assert result.exit_code == 3
    assert result.stdout == "hello\n"
    assert result.stderr == "oops\n"
    assert machines.calls == [
        (
            "exec",
            ("pod:builder", "ls -la"),
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
