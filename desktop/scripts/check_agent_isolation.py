#!/usr/bin/env python3
"""Does the real Claude Code leave out the person's setup the way Lemma starts it?

A local check, not a CI one: when `claude` is installed, it runs it in a fake
home with a marker in every personal place, and fails if a marker reaches
what it would send its model. It needs no sign-in and no network: Claude Code
talks to a stand-in model server on 127.0.0.1 with a made-up key that only
that server sees. (Codex and OpenCode run in the person's own config
folders and have nothing to check here.)

What it holds upstream to is what `desktop/agent-host/src/acp/session_options.rs`
relies on: Claude Code with `--setting-sources project,local --strict-mcp-config`
(the flags `settingSources` and `strictMcpConfig` become) reads none of
`~/.claude/CLAUDE.md`, skills, agents, commands, output styles or the
person's MCP servers, still reads the project's `CLAUDE.md`, and still signs
in through an `apiKeyHelper` given as flag settings.

Run it after bumping a pinned adapter or when an agent's upstream CLI moves:

    python3 desktop/scripts/check_agent_isolation.py
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

MARKER = "PERSONAL_MARKER_7731"
PROJECT_MARKER = "PROJECT_MARKER_7732"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Capture(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        Capture.seen.append({"key": self.headers.get("x-api-key"), "body": body})
        payload = json.dumps(
            {
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "capture"},
            }
        ).encode()
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_arguments: object) -> None:
        pass


def check_claude(root: Path) -> list[str]:
    claude = shutil.which("claude")
    if claude is None:
        print("- claude: not installed, skipped")
        return []
    home = root / "claude-person"
    config = home / ".claude"
    write(config / "CLAUDE.md", MARKER)
    write(
        config / "skills" / "mine" / "SKILL.md",
        f"---\nname: mine\ndescription: {MARKER}\n---\nx\n",
    )
    write(
        config / "agents" / "mine.md",
        f"---\nname: mine\ndescription: {MARKER}\n---\nx\n",
    )
    write(config / "commands" / "mine.md", f"---\ndescription: {MARKER}\n---\nx\n")
    write(
        config / "output-styles" / "mine.md",
        f"---\nname: mine\ndescription: x\n---\n{MARKER}\n",
    )
    write(config / "settings.json", json.dumps({"outputStyle": "mine"}))
    write(
        home / ".claude.json",
        json.dumps(
            {
                "hasCompletedOnboarding": True,
                "mcpServers": {MARKER: {"type": "stdio", "command": "/usr/bin/true"}},
            }
        ),
    )
    project = root / "claude-project"
    write(project / "CLAUDE.md", PROJECT_MARKER)

    server = http.server.HTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = dict(
        os.environ,
        HOME=str(home),
        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_address[1]}",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
    )
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("CLAUDE_CONFIG_DIR", None)
    try:
        subprocess.run(
            [
                claude,
                "-p",
                "hi",
                "--setting-sources",
                "project,local",
                "--strict-mcp-config",
                "--settings",
                json.dumps({"apiKeyHelper": "echo sk-ant-local-check"}),
            ],
            cwd=project,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=180,
        )
    finally:
        server.shutdown()

    failures = []
    if not Capture.seen:
        failures.append("claude: sent nothing to the stand-in model server")
    requests = json.dumps(Capture.seen)
    if MARKER in requests:
        failures.append("claude: the person's setup reached the model")
    if PROJECT_MARKER not in requests:
        failures.append("claude: the project's own CLAUDE.md was lost")
    if Capture.seen and Capture.seen[-1]["key"] != "sk-ant-local-check":
        failures.append("claude: the flag-settings apiKeyHelper did not sign it in")
    print(f"- claude: {'ok' if not failures else 'FAILED'}")
    return failures


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        failures = check_claude(root)
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
