from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import traceback as traceback_module
from types import ModuleType
from typing import Any, TextIO


def _clip_text(value: str | None, budget: int) -> tuple[str | None, int, bool]:
    if value is None:
        return None, budget, False
    encoded = value.encode("utf-8", errors="replace")
    clipped = encoded[:budget]
    return (
        clipped.decode("utf-8", errors="replace"),
        budget - len(clipped),
        len(encoded) > budget,
    )


def _execute(
    code: str,
    output_limit_bytes: int,
    namespace: dict[str, Any],
    environment: dict[str, str],
) -> dict[str, Any]:
    original_stdout = os.dup(1)
    original_stderr = os.dup(2)
    result: str | None = None
    error_name: str | None = None
    error_message: str | None = None
    traceback_text: str | None = None
    state = "succeeded"
    previous_environment = {name: os.environ.get(name) for name in environment}
    os.environ.update(environment)
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(stdout_file.fileno(), 1)
            os.dup2(stderr_file.fileno(), 2)
            tree = ast.parse(code, mode="exec")
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                prefix = ast.Module(body=tree.body[:-1], type_ignores=[])
                if prefix.body:
                    exec(compile(prefix, "<agentbox>", "exec"), namespace, namespace)
                expression = ast.Expression(tree.body[-1].value)
                value = eval(
                    compile(expression, "<agentbox>", "eval"), namespace, namespace
                )
                if value is not None:
                    result = repr(value)
            else:
                exec(compile(tree, "<agentbox>", "exec"), namespace, namespace)
        except BaseException as exc:
            state = "failed"
            error_name = type(exc).__name__
            error_message = str(exc)
            traceback_text = "".join(
                traceback_module.format_exception(type(exc), exc, exc.__traceback__)
            )
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(original_stdout, 1)
            os.dup2(original_stderr, 2)
            os.close(original_stdout)
            os.close(original_stderr)
            for name, previous in previous_environment.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous

        stdout_file.seek(0)
        stdout_bytes = stdout_file.read(output_limit_bytes + 1)
        remaining = max(0, output_limit_bytes - min(len(stdout_bytes), output_limit_bytes))
        stderr_file.seek(0)
        stderr_bytes = stderr_file.read(remaining + 1)
    truncated = len(stdout_bytes) > output_limit_bytes or len(stderr_bytes) > remaining
    stdout_bytes = stdout_bytes[:output_limit_bytes]
    stderr_bytes = stderr_bytes[:remaining]
    text_budget = output_limit_bytes - len(stdout_bytes) - len(stderr_bytes)
    result, text_budget, result_truncated = _clip_text(result, text_budget)
    error_message, text_budget, message_truncated = _clip_text(
        error_message, text_budget
    )
    traceback_text, _text_budget, traceback_truncated = _clip_text(
        traceback_text, text_budget
    )
    truncated = (
        truncated or result_truncated or message_truncated or traceback_truncated
    )
    return {
        "state": state,
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "result": result,
        "error_name": error_name,
        "error_message": error_message,
        "traceback": traceback_text,
        "output_truncated": truncated,
    }


def _namespace() -> dict[str, Any]:
    module = ModuleType("agentbox_session")
    module.__dict__["__builtins__"] = __builtins__
    sys.modules[module.__name__] = module
    return module.__dict__


def run(control_input: TextIO, control_output: TextIO) -> None:
    namespace = _namespace()
    for line in control_input:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            response = _execute(
                str(request["code"]),
                int(request["output_limit_bytes"]),
                namespace,
                {
                    str(item["name"]): str(item["value"])
                    for item in request.get("environment", [])
                },
            )
            response["operation_id"] = str(request["operation_id"])
        except BaseException as exc:
            response = {
                "operation_id": str(request.get("operation_id", "")),
                "state": "failed",
                "stdout": "",
                "stderr": "",
                "result": None,
                "error_name": type(exc).__name__,
                "error_message": str(exc),
                "traceback": "".join(
                    traceback_module.format_exception(type(exc), exc, exc.__traceback__)
                ),
                "output_truncated": False,
            }
        control_output.write(json.dumps(response, separators=(",", ":")) + "\n")
        control_output.flush()


def main() -> None:
    control_input = os.fdopen(os.dup(0), "r", encoding="utf-8", buffering=1)
    control_output = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)
    os.environ.pop("AGENTBOX_RUNTIME_TOKEN", None)
    os.environ.pop("AGENTBOX_RUNTIME_TOKEN_FILE", None)
    run(control_input, control_output)


if __name__ == "__main__":
    main()
