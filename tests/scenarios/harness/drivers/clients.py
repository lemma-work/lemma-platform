"""Drive Lemma through the clients we ship, not just over raw HTTP.

We publish a CLI and two SDKs. Every one of them is a way a real person or a
real script reaches the platform, and each has its own auth handling, its own
argument mapping, and its own serialization — which means each has its own ways
to be wrong that a curl-shaped test cannot see.

These drivers run the shipped clients **as subprocesses in their own
environments**, rather than importing them into this suite. Two reasons, and
both matter:

* It is what a user does. `uv run lemma pods list` is the product; a Python
  import of the CLI's internals is not.
* It keeps their dependency trees out of this suite's. The clients pin their
  own versions, and resolving all three into one virtualenv would test a
  combination nobody ships.

The cost is process startup per call, so these are used for a **conformance
subset** — proof that each client can actually do the core journey — rather than
for every scenario. The API driver stays the workhorse.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
CLI_PROJECT = ROOT / "lemma-cli"
SDK_PROJECT = ROOT / "lemma-python"
TS_PROJECT = ROOT / "lemma-typescript"

JSON = dict[str, Any]


class ClientFailed(AssertionError):
    """A shipped client could not do something the API allows."""


def _client_env() -> dict[str, str]:
    """This suite's own virtualenv, removed.

    `uv run --project X` refuses to use X's environment while `VIRTUAL_ENV`
    points somewhere else — it warns and ignores the project, which is exactly
    the mix-up these drivers exist to avoid. Each client runs in its own.
    """
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    return env


def _run(command: list[str], *, cwd: Path, what: str, timeout: float = 120) -> str:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_client_env(),
    )
    if result.returncode != 0:
        raise ClientFailed(
            f"{what} failed (exit {result.returncode}).\n"
            f"  command: {' '.join(command[:6])} …\n"
            f"  stdout: {result.stdout[-1500:]}\n"
            f"  stderr: {result.stderr[-3000:]}"
        )
    return result.stdout


@dataclass(frozen=True, slots=True)
class CliDriver:
    """The `lemma` command line, run the way a person runs it."""

    base_url: str
    token: str

    def available(self) -> bool:
        return (CLI_PROJECT / "pyproject.toml").is_file()

    def run(self, *args: str, pod: str | None = None, org: str | None = None) -> str:
        # The console script from `[project.scripts] lemma = lemma_cli.cli:main`,
        # which is the entrypoint a user actually has on their PATH. `python -m
        # lemma_cli` is not it — the package has no `__main__`.
        command = [
            "uv",
            "run",
            "--project",
            str(CLI_PROJECT),
            "lemma",
            "--base-url",
            self.base_url,
            "--token",
            self.token,
        ]
        if org:
            command += ["--org", org]
        if pod:
            command += ["--pod", pod]
        command += list(args)
        return _run(command, cwd=CLI_PROJECT, what=f"lemma {' '.join(args)}")

    def json(self, *args: str, pod: str | None = None, org: str | None = None) -> Any:
        output = self.run(*args, "--json", pod=pod, org=org)
        try:
            return json.loads(output)
        except ValueError as error:
            raise ClientFailed(
                f"`lemma {' '.join(args)} --json` did not print JSON.\n"
                f"  got: {output[:1000]}"
            ) from error


@dataclass(frozen=True, slots=True)
class PythonSdkDriver:
    """`lemma_sdk`, exercised in the SDK's own environment."""

    base_url: str
    token: str

    def available(self) -> bool:
        return (SDK_PROJECT / "pyproject.toml").is_file()

    def evaluate(self, body: str) -> Any:
        """Run `body` with a connected `lemma` in scope, and return its `result`.

        The script sets `result` to whatever it wants back; this prints it as
        JSON on a marked line so ordinary SDK logging on stdout cannot be
        mistaken for the answer.
        """
        script = (
            "import json\n"
            "from lemma_sdk import Lemma\n"
            f"lemma = Lemma(base_url={self.base_url!r}, token={self.token!r})\n"
            "result = None\n"
            f"{body}\n"
            "print('<<<RESULT>>>' + json.dumps(result, default=str))\n"
        )
        output = _run(
            ["uv", "run", "--project", str(SDK_PROJECT), "python", "-c", script],
            cwd=SDK_PROJECT,
            what="lemma_sdk script",
        )
        for line in output.splitlines():
            if line.startswith("<<<RESULT>>>"):
                return json.loads(line[len("<<<RESULT>>>") :])
        raise ClientFailed(f"SDK script printed no result.\n  output: {output[:1500]}")


@dataclass(frozen=True, slots=True)
class TypescriptSdkDriver:
    """`lemma-typescript`, exercised through node.

    Needs the package built (`npm run build` in lemma-typescript). `available()`
    reports whether it is, so a scenario can skip with a reason rather than
    failing on a missing dist.
    """

    base_url: str
    token: str

    def available(self) -> bool:
        return (TS_PROJECT / "dist").is_dir()

    def evaluate(self, body: str) -> Any:
        script = (
            # `LemmaClient`, not `Lemma`: that is the name `src/index.ts`
            # exports and the one the package README tells people to import.
            # The old spelling exists nowhere in the SDK, so this script failed
            # on `new Lemma(...)` being undefined long before it reached the
            # API -- which read as the SDK being unloadable and was filed as
            # half of `DEV-SDK-001`.
            "const { LemmaClient, setTestingToken } = require('./dist/index.js');\n"
            # `apiUrl`, and the token injected rather than passed: `LemmaConfig`
            # has no `baseUrl` and no `token` field, so the old spelling built a
            # client pointed nowhere and unauthenticated, and every call came
            # back 401. `setTestingToken` is the SDK's own documented way to
            # supply a bearer token outside a browser session.
            f"setTestingToken({self.token!r});\n"
            f"const lemma = new LemmaClient({{ apiUrl: {self.base_url!r}, "
            f"authUrl: {self.base_url!r} }});\n"
            "(async () => {\n"
            f"{body}\n"
            "})().catch((e) => { console.error(e); process.exit(1); });\n"
        )
        output = _run(
            ["node", "-e", script], cwd=TS_PROJECT, what="lemma-typescript script"
        )
        for line in output.splitlines():
            if line.startswith("<<<RESULT>>>"):
                return json.loads(line[len("<<<RESULT>>>") :])
        raise ClientFailed(f"TS script printed no result.\n  output: {output[:1500]}")
