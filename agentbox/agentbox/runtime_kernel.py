from __future__ import annotations

import ast
import contextlib
import ctypes
import io
import json
import os
import signal
import subprocess
import sys
import time
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, TextIO


_TERMINATION_GRACE_SECONDS = 2.0


def _harden_child_process() -> None:
    """Protect credentials and ensure a Linux child cannot outlive its parent."""
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        # Linux prctl(PR_SET_PDEATHSIG, SIGKILL) and
        # prctl(PR_SET_DUMPABLE, 0). Do both before the first request, which is
        # when the child first receives session credentials.
        libc.prctl(1, signal.SIGKILL, 0, 0, 0)
        libc.prctl(4, 0, 0, 0, 0)
        if os.getppid() == 1:
            os.kill(os.getpid(), signal.SIGKILL)
    except (AttributeError, OSError):
        # This is defense in depth. The sandbox remains the security boundary
        # on platforms that do not expose prctl.
        pass


def _execute_source(source: str, namespace: dict[str, Any]) -> str | None:
    tree = ast.parse(source, filename="<agentbox>", mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        prefix = ast.Module(body=tree.body[:-1], type_ignores=tree.type_ignores)
        ast.fix_missing_locations(prefix)
        if prefix.body:
            exec(compile(prefix, "<agentbox>", "exec"), namespace)

        expression = ast.Expression(tree.body[-1].value)
        ast.fix_missing_locations(expression)
        result = eval(compile(expression, "<agentbox>", "eval"), namespace)
        return repr(result) if result is not None else None

    exec(compile(tree, "<agentbox>", "exec"), namespace)
    return None


def _kernel_main() -> None:
    _harden_child_process()
    module_name = f"__agentbox_kernel_{os.getpid()}__"
    module = types.ModuleType(module_name)
    module.__dict__["__builtins__"] = __builtins__
    sys.modules[module_name] = module
    namespace = module.__dict__

    for raw_request in sys.stdin:
        try:
            request = json.loads(raw_request)
            if not isinstance(request, dict) or request.get("op") != "execute":
                raise ValueError("Unsupported runtime kernel request")
            source = request.get("code")
            cwd = request.get("cwd")
            env = request.get("env")
            if not isinstance(source, str):
                raise ValueError("Runtime kernel code must be a string")
            if not isinstance(cwd, str):
                raise ValueError("Runtime kernel cwd must be a string")
            if not isinstance(env, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in env.items()
            ):
                raise ValueError("Runtime kernel env must be a string mapping")

            stdout = io.StringIO()
            stderr = io.StringIO()
            result_repr: str | None = None
            error_name: str | None = None
            previous_env = os.environ.copy()
            try:
                os.environ.update(env)
                Path(cwd).mkdir(parents=True, exist_ok=True)
                os.chdir(cwd)
                with (
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    try:
                        result_repr = _execute_source(source, namespace)
                    except BaseException as exc:
                        traceback.print_exc(file=stderr)
                        error_name = exc.__class__.__name__
            finally:
                resulting_cwd = os.getcwd()
                os.environ.clear()
                os.environ.update(previous_env)

            response = {
                "ok": error_name is None,
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
                "result": result_repr,
                "error_name": error_name,
                "cwd": resulting_cwd,
            }
        except BaseException as exc:
            response = {
                "ok": False,
                "stdout": "",
                "stderr": traceback.format_exc(),
                "result": None,
                "error_name": exc.__class__.__name__,
                "cwd": os.getcwd(),
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


@dataclass
class RuntimePythonKernel:
    """One stateful Python child, owned by exactly one runtime session."""

    process: subprocess.Popen[str]
    _io_lock: Lock = field(default_factory=Lock)

    @classmethod
    def start(cls) -> RuntimePythonKernel:
        kernel_path = Path(__file__).resolve()
        process = subprocess.Popen(
            [sys.executable, str(kernel_path), "--child"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
            close_fds=True,
        )
        return cls(process=process)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def execute(
        self,
        *,
        code: str,
        env: dict[str, str],
        cwd: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        request = json.dumps(
            {"op": "execute", "code": code, "env": env, "cwd": cwd},
            separators=(",", ":"),
        )
        with self._io_lock:
            if not self.alive or self.process.stdin is None or self.process.stdout is None:
                raise RuntimeError("Python kernel is not running")
            try:
                self.process.stdin.write(request + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError("Python kernel stopped before accepting code") from exc

            line = _readline_with_timeout(self.process.stdout, timeout_seconds)
            if line is None:
                raise TimeoutError(
                    f"Python execution timed out after {timeout_seconds} seconds"
                )
            if not line:
                raise RuntimeError("Python kernel stopped before returning a result")
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Python kernel returned malformed data") from exc
            if not isinstance(response, dict):
                raise RuntimeError("Python kernel returned a non-object result")
            return response

    def terminate(self) -> None:
        process = self.process
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
        if process.poll() is None:
            try:
                process.wait(timeout=_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        while time.monotonic() < deadline and _process_group_exists(process.pid):
            time.sleep(0.01)
        if _process_group_exists(process.pid):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _readline_with_timeout(stream: TextIO, timeout_seconds: int) -> str | None:
    # selector-backed waiting works for the real pipe. A tiny fallback thread is
    # intentionally avoided: an abandoned readline would race the next request.
    import selectors

    selector = selectors.DefaultSelector()
    try:
        selector.register(stream, selectors.EVENT_READ)
        if not selector.select(timeout_seconds):
            return None
        return stream.readline()
    finally:
        selector.close()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--child":
        _kernel_main()
    else:
        raise SystemExit("runtime_kernel.py is an internal AgentBox child process")
