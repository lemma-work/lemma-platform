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
in through an `apiKeyHelper` given as flag settings. That in Claude Code's
"Don't Ask" permission mode, which never asks, a tool of Lemma's server named
in `--allowedTools` as `mcp__<server>__<tool>` -- the rule `allowedTools`
becomes for every tool a run publishes -- runs, while one named in no rule is
denied. And that with its tool search on, Claude Code puts the tools of a
server marked `alwaysLoad`, as Lemma's is, in its first request, while it
holds another server's back for the model to search for.

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


#: Lemma's server and one of its tools, as a run registers and publishes them.
LEMMA_SERVER = "lemma_tools"
LEMMA_TOOL = "lemma_echo"
LEMMA_TOOL_RULE = f"mcp__{LEMMA_SERVER}__{LEMMA_TOOL}"

#: An MCP server serving `LEMMA_TOOL`, which appends each call to the file
#: named by its first argument.
LEMMA_SERVER_SCRIPT = f"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, result = message["method"], {{}}
    if method == "initialize":
        result = {{"protocolVersion": message["params"]["protocolVersion"],
                  "capabilities": {{"tools": {{}}}},
                  "serverInfo": {{"name": "{LEMMA_SERVER}", "version": "1"}}}}
    elif method == "tools/list":
        result = {{"tools": [{{"name": "{LEMMA_TOOL}", "inputSchema": {{"type": "object"}}}}]}}
    elif method == "tools/call":
        with open(sys.argv[1], "a") as calls:
            calls.write(line)
        result = {{"content": [{{"type": "text", "text": "echoed"}}]}}
    print(json.dumps({{"jsonrpc": "2.0", "id": message["id"], "result": result}}), flush=True)
"""


def lemma_server(root: Path, calls: Path, **extra: object) -> dict[str, object]:
    """`LEMMA_SERVER_SCRIPT`, written under `root`, as an `--mcp-config` entry
    that appends each call to `calls`."""
    script = root / "lemma_tools.py"
    write(script, LEMMA_SERVER_SCRIPT)
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(script), str(calls)],
        **extra,
    }


class AsksForLemmasTool(http.server.BaseHTTPRequestHandler):
    """A stand-in model that calls `LEMMA_TOOL_RULE` once, then stops."""

    requests: int = 0

    def do_POST(self) -> None:  # noqa: N802
        AsksForLemmasTool.requests += 1
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        offered = [tool.get("name") for tool in body.get("tools", [])]
        answered = any(
            block.get("type") == "tool_result"
            for message in body.get("messages", [])
            if isinstance(message.get("content"), list)
            for block in message["content"]
        )
        if LEMMA_TOOL_RULE in offered and not answered:
            block = {"type": "tool_use", "id": "toolu_check", "name": LEMMA_TOOL_RULE, "input": {}}
            stop = "tool_use"
        else:
            block, stop = {"type": "text", "text": "done"}, "end_turn"
        message = {
            "id": "msg_check",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "check"),
            "content": [block],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if not body.get("stream"):
            payload = json.dumps(message).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        if block["type"] == "tool_use":
            start = dict(block, input={})
            delta = {"type": "input_json_delta", "partial_json": "{}"}
        else:
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        for event in (
            {"type": "message_start", "message": dict(message, content=[])},
            {"type": "content_block_start", "index": 0, "content_block": start},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ):
            self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())

    def log_message(self, *_arguments: object) -> None:
        pass


def dont_ask_ran_lemmas_tool(root: Path, claude: str, rules: list[str]) -> bool | None:
    """Whether `claude`, in "Don't Ask" mode with these `--allowedTools`, ran
    Lemma's tool when its model asked for it -- or None when it never reached
    the stand-in model at all."""
    home = root / "home"
    write(home / ".claude.json", json.dumps({"hasCompletedOnboarding": True}))
    calls = root / "calls.jsonl"
    calls.unlink(missing_ok=True)
    # Marked as Lemma marks it, so the tool is offered rather than searched for.
    servers = {LEMMA_SERVER: lemma_server(root, calls, alwaysLoad=True)}
    AsksForLemmasTool.requests = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AsksForLemmasTool)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = dict(
        os.environ,
        HOME=str(home),
        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_address[1]}",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
    )
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("CLAUDE_CONFIG_DIR", None)
    allowed = ["--allowedTools", ",".join(rules)] if rules else []
    try:
        subprocess.run(
            [
                claude,
                "-p",
                f"Call the {LEMMA_TOOL} tool.",
                "--permission-mode",
                "dontAsk",
                "--strict-mcp-config",
                "--mcp-config",
                json.dumps({"mcpServers": servers}),
                *allowed,
                "--settings",
                json.dumps({"apiKeyHelper": "echo sk-ant-local-check"}),
            ],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=180,
        )
    finally:
        server.shutdown()
    if AsksForLemmasTool.requests == 0:
        return None
    return calls.exists() and calls.read_text(encoding="utf-8").strip() != ""


def check_claude_dont_ask(root: Path) -> list[str]:
    claude = shutil.which("claude")
    if claude is None:
        print("- claude Don't Ask: not installed, skipped")
        return []
    failures: list[str] = []
    allowed = dont_ask_ran_lemmas_tool(root / "allowed", claude, [LEMMA_TOOL_RULE])
    unruled = dont_ask_ran_lemmas_tool(root / "unruled", claude, [])
    if allowed is None or unruled is None:
        failures.append("claude Don't Ask: sent nothing to the stand-in model server")
    else:
        if not allowed:
            failures.append(
                f"claude Don't Ask: {LEMMA_TOOL_RULE} in --allowedTools did not let the tool run"
            )
        if unruled:
            # Without this the first check would hold whatever Claude Code did.
            failures.append("claude Don't Ask: a tool named in no rule ran without asking")
    print(f"- claude Don't Ask: {'ok' if not failures else 'FAILED'}")
    return failures


#: A server of the person's own beside Lemma's, which nothing marks.
THEIR_SERVER = "theirs"


def check_claude_always_load(root: Path) -> list[str]:
    claude = shutil.which("claude")
    if claude is None:
        print("- claude alwaysLoad: not installed, skipped")
        return []
    home = root / "home"
    write(home / ".claude.json", json.dumps({"hasCompletedOnboarding": True}))
    calls = root / "calls.jsonl"
    servers = {
        LEMMA_SERVER: lemma_server(root, calls, alwaysLoad=True),
        THEIR_SERVER: lemma_server(root, calls),
    }
    Capture.seen = []
    model = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    environment = dict(
        os.environ,
        HOME=str(home),
        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{model.server_address[1]}",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        # On, as it is against Anthropic's own API. Behind a base URL it does
        # not know, which this stand-in's is, Claude Code may leave it off.
        ENABLE_TOOL_SEARCH="true",
    )
    environment.pop("ANTHROPIC_API_KEY", None)
    environment.pop("CLAUDE_CONFIG_DIR", None)
    try:
        subprocess.run(
            [
                claude,
                "-p",
                "hi",
                "--strict-mcp-config",
                "--mcp-config",
                json.dumps({"mcpServers": servers}),
                "--settings",
                json.dumps({"apiKeyHelper": "echo sk-ant-local-check"}),
            ],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=180,
        )
    finally:
        model.shutdown()

    failures = []
    if not Capture.seen:
        failures.append("claude alwaysLoad: sent nothing to the stand-in model server")
    else:
        first = json.loads(Capture.seen[0]["body"])
        offered = {tool.get("name") for tool in first.get("tools", [])}
        if "ToolSearch" not in offered or f"mcp__{THEIR_SERVER}__{LEMMA_TOOL}" in offered:
            # Without this the next check would hold whatever Claude Code did.
            failures.append("claude alwaysLoad: tool search was off, so nothing was held back")
        elif LEMMA_TOOL_RULE not in offered:
            failures.append(
                f"claude alwaysLoad: {LEMMA_TOOL_RULE} was held back behind tool search"
            )
    print(f"- claude alwaysLoad: {'ok' if not failures else 'FAILED'}")
    return failures


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        failures = (
            check_claude(root)
            + check_claude_dont_ask(root / "dont-ask")
            + check_claude_always_load(root / "always-load")
        )
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
