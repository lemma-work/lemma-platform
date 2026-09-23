"""A host agent's tool calls, in the shape Lemma's own tools render in.

Every card is keyed on the tool's name and reads fixed fields: a terminal card
reads `cmd` as a string and `exit_code`/`stdout`/`stderr` from the result. ACP
adapters report the same facts in their own places, so each is translated once,
here, rather than every card learning every adapter.
"""

from __future__ import annotations

from app.modules.agent.infrastructure.harnesses.agent_host.tool_payload import (
    tool_args,
    tool_name_from_payload,
    tool_result,
)

CODEX_PWD = {
    "kind": "execute",
    "title": "pwd",
    "rawInput": {"command": ["bash", "-lc", "pwd"], "cwd": "/Users/me/project"},
}


def test_a_shell_script_is_the_command_a_card_shows():
    assert tool_name_from_payload(CODEX_PWD) == "exec_command"
    args = tool_args(CODEX_PWD, "exec_command")
    assert args == {"cmd": "pwd", "cwd": "/Users/me/project"}


def test_any_other_argv_is_joined_as_typed():
    payload = {
        "kind": "execute",
        "rawInput": {"command": ["git", "commit", "-m", "a b"]},
    }
    assert tool_args(payload, "exec_command") == {"cmd": "git commit -m 'a b'"}


def test_a_call_with_no_command_argument_is_named_by_its_title():
    payload = {"kind": "execute", "title": "`npm test`", "rawInput": {}}
    assert tool_args(payload, "exec_command") == {"cmd": "npm test"}


def test_a_string_command_is_kept():
    payload = {"kind": "execute", "rawInput": {"command": "ls -la"}}
    assert tool_args(payload, "exec_command") == {"cmd": "ls -la"}


def test_a_completed_command_reports_its_output_and_exit_code():
    payload = {
        **CODEX_PWD,
        "status": "completed",
        "rawOutput": {"exit_code": 0, "stdout": "/Users/me/project\n", "stderr": ""},
    }
    assert tool_result("exec_command", "COMPLETED", payload) == {
        "exit_code": 0,
        "stdout": "/Users/me/project\n",
    }


def test_a_failed_command_keeps_what_it_printed():
    """The card used to show only "failed": the output was thrown away."""
    payload = {
        **CODEX_PWD,
        "status": "failed",
        "rawOutput": {
            "exit_code": 127,
            "aggregated_output": "bash: lemma: command not found\n",
        },
    }
    result = tool_result("exec_command", "FAILED", payload)
    assert result["exit_code"] == 127
    assert result["stdout"] == "bash: lemma: command not found\n"
    assert result["success"] is False
    assert result["error"] == "exited with code 127"


def test_output_reported_as_content_blocks_is_read_too():
    payload = {
        "kind": "execute",
        "status": "failed",
        "content": [
            {
                "type": "content",
                "content": {"type": "text", "text": "permission denied"},
            }
        ],
    }
    result = tool_result("exec_command", "FAILED", payload)
    assert result["stdout"] == "permission denied"
    assert result["error"] == "failed"


def test_an_adapters_own_error_is_the_sentence():
    payload = {
        "kind": "execute",
        "status": "failed",
        "error": {"message": "sandbox refused"},
    }
    assert tool_result("exec_command", "FAILED", payload)["error"] == "sandbox refused"


def test_acp_categories_use_lemmas_tool_names():
    for kind, name in [
        ("read", "read_file"),
        ("edit", "edit_file"),
        ("search", "grep"),
    ]:
        assert tool_name_from_payload({"kind": kind}) == name


def test_a_file_tool_takes_its_path_from_locations():
    payload = {
        "kind": "read",
        "locations": [{"path": "/Users/me/project/app.py", "line": 3}],
    }
    assert tool_args(payload, "read_file") == {"file_path": "/Users/me/project/app.py"}


def test_a_file_tools_own_path_wins():
    payload = {
        "kind": "edit",
        "rawInput": {"file_path": "/a.py"},
        "locations": [{"path": "/b.py"}],
    }
    assert tool_args(payload, "edit_file") == {"file_path": "/a.py"}


def test_a_denied_call_says_so():
    result = tool_result("web_fetch", "DENIED", {"kind": "fetch"})
    assert result == {"success": False, "error": "not allowed"}


def test_a_command_output_in_an_unrecognised_shape_is_kept():
    envelope = [{"type": "text", "text": "hello from mcp"}]
    result = tool_result("exec_command", "COMPLETED", {"rawOutput": envelope})
    assert result == {"output": envelope}


# What codex-acp 1.1.7 actually sends (dist/index.js, createCommandExecution*):
# `command` is one string with the shell still on it, and the completion carries
# `formatted_output` and an `exit_code` that is null when the command never ran.
CODEX_EXEC_START = {
    "kind": "execute",
    "title": "ls -la",
    "status": "in_progress",
    "rawInput": {"command": "/bin/zsh -lc 'ls -la'", "cwd": "/Users/me/project"},
}


def test_codex_sends_the_command_as_one_string_with_its_shell():
    args = tool_args(CODEX_EXEC_START, "exec_command")
    assert args == {"cmd": "ls -la", "cwd": "/Users/me/project"}


def test_codex_completion_output_is_the_terminal_text():
    payload = {
        "status": "completed",
        "rawOutput": {"formatted_output": "total 8\n", "exit_code": 0},
    }
    assert tool_result("exec_command", "COMPLETED", payload) == {
        "exit_code": 0,
        "stdout": "total 8\n",
    }


def test_an_empty_stdout_does_not_hide_the_aggregated_output():
    payload = {
        "rawOutput": {"stdout": "", "aggregated_output": "boom\n", "exit_code": 1}
    }
    result = tool_result("exec_command", "FAILED", payload)
    assert result["stdout"] == "boom\n"
    assert result["exit_code"] == 1
    assert result["error"] == "exited with code 1"


def test_a_command_that_never_ran_says_so_rather_than_failed():
    payload = {"rawOutput": {"formatted_output": "", "exit_code": None}}
    result = tool_result("exec_command", "FAILED", payload)
    assert result["error"] == "did not run to completion (declined or stopped)"


def test_a_malformed_exit_code_is_ignored_not_raised():
    payload = {"rawOutput": {"exit_code": "--1", "formatted_output": "x"}}
    assert tool_result("exec_command", "COMPLETED", payload) == {"stdout": "x"}
    payload = {"rawOutput": {"exit_code": "-2", "formatted_output": "x"}}
    assert tool_result("exec_command", "COMPLETED", payload)["exit_code"] == -2


def test_an_absurdly_long_exit_code_is_ignored_not_raised():
    payload = {"rawOutput": {"exit_code": "9" * 5000, "formatted_output": "x"}}
    assert tool_result("exec_command", "COMPLETED", payload) == {"stdout": "x"}
