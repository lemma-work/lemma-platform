from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app

_runner = CliRunner()


def cli(args, *, base_url, token, pod=None, org=None):
    """Invoke the CLI against a real server.

    Args:
        args: List of command + subcommand + args (without global flags)
        base_url: The backend server URL
        token: Bearer token for auth
        pod: Optional pod id/name to pass as --pod
        org: Optional org id/name to pass as --org
    Returns:
        typer.testing.Result
    """
    global_args = ["--base-url", base_url, "--token", token]
    if org:
        global_args += ["--org", org]
    if pod:
        global_args += ["--pod", pod]
    return _runner.invoke(app, global_args + list(args))


def cli_json(args, *, base_url, token, pod=None, org=None):
    """Run a CLI command with --json and return the parsed JSON payload.

    Asserts the command succeeded (exit 0) so callers can focus on the payload.
    The global --json flag is hoisted by the CLI root, so it works in any
    position; we append it to ``args`` to keep ``--json`` next to the command.
    """
    result = cli([*args, "--json"], base_url=base_url, token=token, pod=pod, org=org)
    # The failure message has to read stderr: diagnostics go there so that the
    # JSON on stdout stays parseable, which means a failed run has an EMPTY
    # stdout and the reason is on the other stream.
    assert result.exit_code == 0, (
        f"CLI {args} failed with exit {result.exit_code}:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def items_of(payload: Any) -> list[dict[str, Any]]:
    """Extract list rows from a CLI --json payload, handling both shapes.

    The CLI emits the SDK payload as-is: some ``list`` commands return a bare
    list ``[...]`` and others a page object ``{"items": [...]}``. This mirrors
    ``lemma_cli.cli_core.io.list_items`` so e2e assertions stay shape-agnostic.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []
