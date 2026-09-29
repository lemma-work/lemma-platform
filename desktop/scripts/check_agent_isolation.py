#!/usr/bin/env python3
"""Do the real coding agents leave out the person's setup the way Lemma starts them?

A local check, not a CI one: it runs whichever of `codex` and `claude` this
machine has installed, each in a fake home with a marker in every personal
place, and fails if a marker reaches what the agent would send its model. It
needs no sign-in and no network. Codex renders its prompt locally
(`codex debug prompt-input`), and Claude Code talks to a stand-in model
server on 127.0.0.1 with a made-up key that only that server sees.

What it holds upstream to is what `desktop/agent-host/src/acp/agent_homes.rs`
and `session_options.rs` rely on:

- Codex in a `CODEX_HOME` of Lemma's (no `AGENTS.md`, no `rules/`, no MCP
  servers) does not read the person's `~/.codex/AGENTS.md`, and does when
  pointed at the person's own home, which is what makes the check mean
  something.
- Claude Code with `--setting-sources project,local --strict-mcp-config`
  (the flags `settingSources` and `strictMcpConfig` become) reads none of
  `~/.claude/CLAUDE.md`, skills, agents, commands, output styles or the
  person's MCP servers, still reads the project's `CLAUDE.md`, and still
  signs in through an `apiKeyHelper` given as flag settings.

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


def codex_prompt(codex: str, home: Path, codex_home: Path, project: Path) -> str:
    environment = dict(os.environ, HOME=str(home), CODEX_HOME=str(codex_home))
    completed = subprocess.run(
        [codex, "debug", "prompt-input", "hi"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"codex debug prompt-input failed: {completed.stderr[-800:]}"
        )
    return completed.stdout


def check_codex(root: Path) -> list[str]:
    codex = shutil.which("codex")
    if codex is None:
        print("- codex: not installed, skipped")
        return []
    home = root / "codex-person"
    write(home / ".codex" / "AGENTS.md", MARKER)
    write(
        home / ".codex" / "rules" / "default.rules",
        f'prefix_rule(pattern=["{MARKER}"])\n',
    )
    write(
        home / ".codex" / "config.toml",
        f'[mcp_servers.{MARKER.lower()}]\ncommand = "/usr/bin/true"\n',
    )
    lemmas = root / "codex-lemma"
    lemmas.mkdir()
    project = root / "codex-project"
    write(project / "AGENTS.md", PROJECT_MARKER)

    failures = []
    own = codex_prompt(codex, home, home / ".codex", project)
    if MARKER not in own:
        failures.append(
            "codex: the person's own home did not load its AGENTS.md; the check proves nothing"
        )
    isolated = codex_prompt(codex, home, lemmas, project)
    if MARKER in isolated:
        failures.append("codex: a Lemma CODEX_HOME still loaded the person's setup")
    if PROJECT_MARKER not in isolated:
        failures.append("codex: the project's own AGENTS.md was lost")
    print(f"- codex: {'ok' if not failures else 'FAILED'}")
    return failures


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
        failures = check_codex(root) + check_claude(root)
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
