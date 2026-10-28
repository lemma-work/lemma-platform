#!/usr/bin/env python3
"""Deterministic ACP v1 agent used by the Rust integration suite."""

import json
import os
import pathlib
import subprocess
import sys
import time

# ACP uses UTF-8 even when Windows gives redirected Python pipes a legacy codec.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

log_path = pathlib.Path(sys.argv[1])
# What this agent has forgotten. "forget-session" makes `session/load` fail the
# way a real provider's does when its rollout file has been pruned or its
# session deleted from disk -- the case Lemma cannot predict and withholds the
# conversation's history for.
#
# The other modes are about starting, and share state between every copy
# started against one log directory, the way copies of a real agent share its
# home directory:
#
# - "contended": a second copy opening a session while another is doing so
#   fails with a timeout, as Codex does ("workspace routing discovery timed
#   out") when several start together;
# - "times-out-once": the first `session/new` among all copies times out, and
#   every later one succeeds;
# - "misconfigured": `session/new` always fails, for a reason no retry fixes.
MODE = sys.argv[2] if len(sys.argv) > 2 else "remembers"
START_LOCK = log_path.parent / "starting.lock"
TIMED_OUT_ONCE = log_path.parent / "timed-out-once"


def timed_out(details):
    return {"code": -32603, "message": "Internal error", "data": {"details": details}}


def start_failure():
    """The error this copy's `session/new` fails with, or None to answer it."""
    if MODE == "misconfigured":
        return {
            "code": -32602,
            "message": "Invalid params",
            "data": 'unknown model provider "nope" in config.toml',
        }
    if MODE == "times-out-once":
        try:
            os.close(os.open(TIMED_OUT_ONCE, os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            return None
        return timed_out("workspace routing discovery timed out")
    if MODE == "contended":
        try:
            os.close(os.open(START_LOCK, os.O_CREAT | os.O_EXCL))
        except FileExistsError:
            return timed_out("workspace routing discovery timed out")
        # Long enough that an ungated burst reliably overlaps it.
        time.sleep(0.5)
        os.remove(START_LOCK)
    return None


def effort(current, levels):
    return {
        "id": "effort",
        "name": "Effort",
        "category": "thought_level",
        "type": "select",
        "currentValue": current,
        "options": [{"value": level, "name": level.title()} for level in levels],
    }


# "per-model-options": each model offers its own reasoning levels, as Claude's
# and Codex's adapters do, and one model's cannot be read at all.
PER_MODEL_EFFORT = {
    "fake-1": ("medium", ["low", "medium"]),
    "fake-2": ("high", ["low", "high", "max"]),
}
PER_MODEL_CATALOG = [
    {"value": "fake-1", "name": "Fake 1"},
    {"value": "fake-2", "name": "Fake 2"},
    {"value": "fake-broken", "name": "Fake Broken"},
]


# "slow-model-options": six models that each take 2.5s to switch to, as Claude
# Code's adapter takes about a second each -- more than the probe can wait for.
SLOW_MODELS = [f"slow-{n}" for n in range(1, 7)]
SLOW_SWITCH_SECONDS = 2.5


def slow_model_options(model):
    return [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": model,
            "options": [{"value": name, "name": name} for name in SLOW_MODELS],
        },
        effort("low", ["low", "high"]),
    ]


def per_model_options(model):
    current, levels = PER_MODEL_EFFORT[model]
    return [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": model,
            "options": PER_MODEL_CATALOG,
        },
        effort(current, levels),
    ]


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id, value):
    emit({"jsonrpc": "2.0", "id": request_id, "result": value})


def record(message):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, separators=(",", ":")) + "\n")


for raw_line in sys.stdin:
    message = json.loads(raw_line)
    record(message)
    method = message.get("method")
    request_id = message.get("id")
    failure = start_failure() if method == "session/new" else None
    if method == "initialize":
        # What the host set up for this agent beyond ACP, so a test can see
        # the switches a real adapter would read (see `acp::session_options`).
        record(
            {
                "environment": {
                    name: value
                    for name, value in os.environ.items()
                    if name
                    in {
                        "BASH_ENV",
                        "CODEX_CONFIG",
                        "CODEX_HOME",
                        "PATH",
                        "XDG_CONFIG_HOME",
                        "ZDOTDIR",
                    }
                    or name.startswith(
                        ("OPENCODE_", "CLAUDE_CODE_", "LEMMA_", "LEMMAHOST_")
                    )
                }
            }
        )
        # Which `lemma` a command finds in this agent's shells, as a harness
        # runs it: through the person's shell and its startup files.
        if os.environ.get("FAKE_ACP_PROBE_LEMMA"):
            found = {}
            for shell, flag in (
                ("/bin/zsh", "-c"),
                ("/bin/zsh", "-lc"),
                ("/bin/bash", "-lc"),
            ):
                if os.path.isfile(shell):
                    found[f"{shell} {flag}"] = subprocess.run(
                        [shell, flag, "command -v lemma"],
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
            record({"shell_lemma": found})
        result(
            request_id,
            {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": MODE == "forget-session",
                    "mcpCapabilities": {"http": True, "sse": True},
                    "promptCapabilities": {"image": False, "audio": False},
                },
                "authMethods": [],
                "agentInfo": {"name": "fake-acp", "version": "1.0.0"},
            },
        )
    elif failure is not None:
        emit({"jsonrpc": "2.0", "id": request_id, "error": failure})
    elif method == "session/load" and MODE == "forget-session":
        emit(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "session not found"},
            }
        )
    elif method == "session/new" and MODE == "slow-model-options":
        result(
            request_id,
            {"sessionId": "fake-session", "configOptions": slow_model_options("slow-1")},
        )
    elif method == "session/set_config_option" and MODE == "slow-model-options":
        time.sleep(SLOW_SWITCH_SECONDS)
        result(
            request_id,
            {"configOptions": slow_model_options(message["params"]["value"])},
        )
    elif method == "session/new" and MODE == "per-model-options":
        result(
            request_id,
            {"sessionId": "fake-session", "configOptions": per_model_options("fake-1")},
        )
    elif method == "session/set_config_option" and MODE == "per-model-options":
        value = message["params"]["value"]
        if value in PER_MODEL_EFFORT:
            result(request_id, {"configOptions": per_model_options(value)})
        else:
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"cannot switch to {value}"},
                }
            )
    elif method in {"session/new", "session/load"}:
        result(
            request_id,
            {
                "sessionId": "fake-session",
                "configOptions": [
                    {
                        "id": "model",
                        "name": "Model",
                        "description": "Dynamic model catalog",
                        "category": "model",
                        "type": "select",
                        "currentValue": "fake-1",
                        "options": [
                            {"value": "fake-1", "name": "Fake 1"},
                            {"value": "fake-2", "name": "Fake 2"},
                        ],
                    }
                ],
            },
        )
    elif method == "session/set_config_option":
        result(
            request_id,
            {
                "configOptions": [
                    {
                        "id": "model",
                        "name": "Model",
                        "category": "model",
                        "type": "select",
                        "currentValue": message["params"]["value"],
                        "options": [{"value": "fake-2", "name": "Fake 2"}],
                    }
                ]
            },
        )
    elif method == "session/prompt":
        future = MODE == "future"
        if future:
            # An agent newer than the host: an update kind, a content block
            # type and a permission option kind ACP did not define when the
            # host was built. The first two are skipped; a request offering
            # only the third is answered `cancelled` without asking anyone.
            for update in (
                {"sessionUpdate": "agent_reflection", "text": "do not log me"},
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "hologram", "data": "do not log me"},
                },
            ):
                emit(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {"sessionId": "fake-session", "update": update},
                    }
                )
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": 901,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "fake-session",
                        "toolCall": {"toolCallId": "future-tool", "kind": "browse"},
                        "options": [
                            {
                                "optionId": "forever",
                                "name": "Allow for the session",
                                "kind": "allow_session",
                            }
                        ],
                    },
                }
            )
            record(json.loads(sys.stdin.readline()))
        options = [
            {
                "optionId": "allow_once",
                "name": "Allow once",
                "kind": "allow_once",
            }
        ]
        if future:
            options.append(
                {"optionId": "forever", "name": "Always", "kind": "allow_session"}
            )
        emit(
            {
                "jsonrpc": "2.0",
                "id": 900,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "fake-session",
                    "toolCall": {
                        "toolCallId": "native-shell",
                        "status": "pending",
                        "title": "Run native shell",
                    },
                    "options": options,
                },
            }
        )
        permission_response = json.loads(sys.stdin.readline())
        record(permission_response)
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "thinking"},
                    },
                },
            }
        )
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "fake-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hello from fake"},
                    },
                },
            }
        )
        if future:
            result(
                request_id,
                {
                    "stopReason": "budget_exhausted",
                    "usage": {"inputTokens": 3, "outputTokens": 2.0},
                    "brandNew": {"x": 1},
                },
            )
        else:
            result(request_id, {"stopReason": "end_turn"})
    else:
        result(
            request_id,
            {
                "error": {
                    "code": -32601,
                    "message": f"unsupported method {method}",
                }
            },
        )
