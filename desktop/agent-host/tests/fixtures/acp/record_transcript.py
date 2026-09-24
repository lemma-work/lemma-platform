#!/usr/bin/env python3
"""Record what a real ACP agent says during one scripted turn.

The golden transcripts under this directory are the ground truth the
per-adapter normalizers in `src/normalize` are held to. They have to come from
the real adapters: every tool-naming bug the normalizers exist to fix was a
guess about adapter output that turned out wrong.

    python3 record_transcript.py \
        --adapter claude-code \
        --out claude-code@0.62.0/tools.jsonl \
        -- node /path/to/claude-agent-acp

The recorder plays the client side of ACP: it opens a session in a scratch
directory with the stand-in Lemma MCP server attached, sends one prompt that
asks the agent to use every kind of tool the normalizers map, approves every
permission request, and writes each message the agent sent -- plus the
`session/new` answer, which carries its models and config options -- as one
JSON line. Paths, session ids and host names are rewritten to fixed
placeholders so a transcript is reproducible and carries nothing of the machine
it was recorded on.

It spends a little real model quota and needs the agent to be signed in. See
"Golden transcripts" in `docs/architecture/agent-host-events.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
FAKE_MCP = HERE / "fake_lemma_mcp.py"
FIXTURE_CWD = "/workspace/fixture"
FIXTURE_HOME = "/home/fixture"

PROMPTS = {"tools": """You are being recorded to test a tool-call integration. Do each step
below in order, using the tool named, and do not skip any. Keep every message
short.

1. Make a short todo list (plan) of these steps first, using your own todo or
   plan tool if you have one.
2. Run the shell command `echo hello-lemma` with your own shell tool.
3. Create a file named notes.txt containing the single word: one
4. Edit notes.txt, replacing the word one with the word two.
5. Read notes.txt with your file-reading tool.
6. Find files matching *.txt with your own file search or glob tool.
7. Search file contents for the word two with your own grep or search tool.
8. Call the MCP tool lemma_exec_command from the lemma_tools server with
   cmd set to: echo from-mcp
9. Call the MCP tool lemma_display_resource from the lemma_tools server with
   type MARKDOWN and content: # recorded
10. Reply with the single word: done
""",
    "edits": """You are being recorded to test a tool-call integration. Do each step
below in order and keep every message short.

1. Create a file named notes.txt containing the single line: one
   Use your file-editing or patch tool, not the shell.
2. Change that line to: two
   Again use your file-editing or patch tool, not the shell.
3. Search the web for the phrase "Agent Client Protocol" with your web search
   tool, and fetch https://agentclientprotocol.com if you have a fetch tool.
4. Reply with the single word: done
""",
}


#: How many choices of a select config option a transcript keeps. The model
#: list is the recorder's account's own configuration, not the adapter's
#: behaviour, and hundreds of entries of it would bury what the file is for.
KEPT_OPTION_CHOICES = 3


def redact_machine(message: dict) -> dict:
    """Drop what describes the recording machine rather than the adapter.

    `available_commands_update` lists the recorder's own installed skills and
    commands, and a select option lists every model its account can reach.
    Neither shape matters to a normalizer beyond "a list arrived", so both are
    cut down to a stable stand-in.
    """
    params = message.get("params")
    update = params.get("update") if isinstance(params, dict) else None
    if isinstance(update, dict) and update.get("sessionUpdate") == "available_commands_update":
        update["availableCommands"] = [
            {"name": "recorded", "description": "Recorded command list removed."}
        ]
    result = message.get("result")
    if isinstance(result, dict):
        for option in result.get("configOptions") or []:
            if isinstance(option, dict) and isinstance(option.get("options"), list):
                option["options"] = option["options"][:KEPT_OPTION_CHOICES]
        models = result.get("models")
        if isinstance(models, dict) and isinstance(models.get("availableModels"), list):
            models["availableModels"] = models["availableModels"][:KEPT_OPTION_CHOICES]
    return message


def sanitize(value: object, replacements: list[tuple[str, str]]) -> object:
    if isinstance(value, str):
        for needle, replacement in replacements:
            value = value.replace(needle, replacement)
        return value
    if isinstance(value, list):
        return [sanitize(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item, replacements) for key, item in value.items()}
    return value


class Agent:
    def __init__(self, command: list[str], cwd: str, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.inbox: queue.Queue[dict] = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.inbox.put(json.loads(line))
            except json.JSONDecodeError:
                sys.stderr.write(f"[agent stdout] {line}\n")

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            sys.stderr.write(f"[agent] {line}")

    def send(self, message: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> int:
        self.next_id += 1
        self.send(
            {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        )
        return self.next_id


def approve(options: list[dict]) -> dict:
    for preferred in ("allow_always", "allow_once"):
        for option in options:
            if option.get("kind") == preferred:
                return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
    if options:
        return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--prompt", choices=sorted(PROMPTS), default="tools")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = [part for part in arguments.command if part != "--"]
    if not command:
        parser.error("the adapter command goes after --")

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="acp-record-"))
    workdir = scratch / "fixture"
    workdir.mkdir()
    real_workdir = str(workdir.resolve())
    env = dict(os.environ)
    for pair in arguments.env:
        key, _, value = pair.partition("=")
        env[key] = value
    replacements = sorted(
        {
            (real_workdir, FIXTURE_CWD),
            (str(workdir), FIXTURE_CWD),
            # Some adapters title a call with the path relative to `/`.
            (real_workdir.lstrip("/"), FIXTURE_CWD.lstrip("/")),
            (str(workdir).lstrip("/"), FIXTURE_CWD.lstrip("/")),
            (str(pathlib.Path.home()), FIXTURE_HOME),
            (str(FAKE_MCP), "/opt/lemma/fake_lemma_mcp.py"),
            (sys.executable, "/usr/bin/python3"),
        },
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    agent = Agent(command, real_workdir, env)
    records: list[dict] = []
    session_id: str | None = None

    def record(direction: str, message: dict) -> None:
        records.append({"direction": direction, "message": redact_machine(message)})

    init_id = agent.request(
        "initialize",
        {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": {"name": "lemma-agent-host-recorder", "version": "1"},
        },
    )
    new_id: int | None = None
    prompt_id: int | None = None
    deadline = time.monotonic() + arguments.timeout
    finished = False
    while time.monotonic() < deadline and not finished:
        try:
            message = agent.inbox.get(timeout=1.0)
        except queue.Empty:
            if agent.process.poll() is not None:
                sys.stderr.write("agent exited before the turn finished\n")
                break
            continue
        message_id = message.get("id")
        method = message.get("method")
        if method is None and message_id == init_id:
            record("agent", message)
            new_id = agent.request(
                "session/new",
                {
                    "cwd": real_workdir,
                    "mcpServers": [
                        {
                            "name": "lemma_tools",
                            "command": sys.executable,
                            "args": [str(FAKE_MCP)],
                            "env": [],
                        }
                    ],
                },
            )
        elif method is None and message_id == new_id:
            record("agent", message)
            if "error" in message:
                sys.stderr.write(f"session/new failed: {message['error']}\n")
                break
            session_id = message["result"]["sessionId"]
            prompt_id = agent.request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": PROMPTS[arguments.prompt]}],
                },
            )
        elif method is None and message_id == prompt_id:
            record("agent", message)
            finished = True
        elif method == "session/request_permission":
            record("agent", message)
            response = {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": approve(message.get("params", {}).get("options", [])),
            }
            record("client", response)
            agent.send(response)
        elif method is not None and message_id is not None:
            # A request this client did not advertise support for.
            record("agent", message)
            agent.send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": "not supported by recorder"},
                }
            )
        else:
            record("agent", message)

    agent.process.terminate()
    try:
        agent.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        agent.process.kill()
    shutil.rmtree(scratch, ignore_errors=True)
    if not finished:
        sys.stderr.write("the turn did not finish; not writing a transcript\n")
        return 1

    if session_id:
        replacements.append((session_id, "session-fixture"))
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    with arguments.out.open("w", encoding="utf-8") as handle:
        header = {
            "adapter": arguments.adapter,
            "recorded": time.strftime("%Y-%m-%d"),
            "prompt": arguments.prompt,
        }
        handle.write(json.dumps({"header": header}) + "\n")
        for entry in records:
            cleaned = sanitize(entry, replacements)
            handle.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} messages to {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
