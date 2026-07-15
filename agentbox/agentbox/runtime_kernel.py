from __future__ import annotations

import ast
import contextlib
import ctypes
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO, Iterator, TextIO


_TERMINATION_GRACE_SECONDS = 2.0
_REQUEST_FD_ENV = "_AGENTBOX_KERNEL_REQUEST_FD"
_RESPONSE_FD_ENV = "_AGENTBOX_KERNEL_RESPONSE_FD"


def _harden_child_process() -> None:
    """Protect credentials before the kernel receives session environment."""
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        # Mark the process non-dumpable before the first request, which is when
        # it first receives session credentials. Do not use PR_SET_PDEATHSIG
        # here: ThreadingHTTPServer starts a kernel from a request thread and
        # Linux associates that signal with the creating *thread*. The kernel
        # would therefore be killed as soon as the request completed. The
        # private close-on-exec request pipe provides parent-lifetime cleanup,
        # while explicit session/timeout cleanup terminates the process group.
        libc.prctl(4, 0, 0, 0, 0)
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


@contextlib.contextmanager
def _redirect_process_output(
    stdout_target: BinaryIO,
    stderr_target: BinaryIO,
) -> Iterator[None]:
    """Redirect Python, native, and descendant output for one invocation."""
    previous_stdout = sys.stdout
    previous_stderr = sys.stderr
    saved_stdout_fd: int | None = None
    saved_stderr_fd: int | None = None
    with contextlib.suppress(Exception):
        previous_stdout.flush()
    with contextlib.suppress(Exception):
        previous_stderr.flush()
    try:
        saved_stdout_fd = os.dup(1)
        saved_stderr_fd = os.dup(2)
        os.dup2(stdout_target.fileno(), 1)
        os.dup2(stderr_target.fileno(), 2)
        yield
    finally:
        # User code may replace or close sys.stdout/sys.stderr. Flush whatever
        # remains while fd 1/2 still point at this invocation's capture files,
        # then restore both the descriptors and the interpreter objects.
        with contextlib.suppress(Exception):
            sys.stdout.flush()
        with contextlib.suppress(Exception):
            sys.stderr.flush()
        if saved_stdout_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(saved_stdout_fd, 1)
            os.close(saved_stdout_fd)
        if saved_stderr_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(saved_stderr_fd, 2)
            os.close(saved_stderr_fd)
        sys.stdout = previous_stdout
        sys.stderr = previous_stderr


def _read_capture(stream: BinaryIO) -> str:
    stream.flush()
    stream.seek(0)
    return stream.read().decode("utf-8", errors="replace")


def _execute_request(
    request: dict[str, Any],
    namespace: dict[str, Any],
) -> dict[str, Any]:
    if request.get("op") != "execute":
        raise ValueError("Unsupported runtime kernel request")
    source = request.get("code")
    cwd = request.get("cwd")
    env = request.get("env")
    if not isinstance(source, str):
        raise ValueError("Runtime kernel code must be a string")
    if not isinstance(cwd, str):
        raise ValueError("Runtime kernel cwd must be a string")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ValueError("Runtime kernel env must be a string mapping")

    result_repr: str | None = None
    error_name: str | None = None
    previous_env = os.environ.copy()
    resulting_cwd = os.getcwd()
    with (
        tempfile.TemporaryFile(mode="w+b") as stdout_capture,
        tempfile.TemporaryFile(mode="w+b") as stderr_capture,
    ):
        try:
            os.environ.update(env)
            Path(cwd).mkdir(parents=True, exist_ok=True)
            os.chdir(cwd)
            with _redirect_process_output(stdout_capture, stderr_capture):
                try:
                    result_repr = _execute_source(source, namespace)
                except BaseException as exc:
                    traceback.print_exc()
                    error_name = exc.__class__.__name__
        finally:
            resulting_cwd = os.getcwd()
            os.environ.clear()
            os.environ.update(previous_env)

        return {
            "ok": error_name is None,
            "stdout": _read_capture(stdout_capture),
            "stderr": _read_capture(stderr_capture),
            "result": result_repr,
            "error_name": error_name,
            "cwd": resulting_cwd,
        }


def _kernel_main(request_fd: int, response_fd: int) -> None:
    _harden_child_process()
    # These descriptors must remain private to the kernel. In particular,
    # user-created descendants must not keep the control pipes open after the
    # kernel exits or learn the descriptor numbers through sys.argv/env.
    os.set_inheritable(request_fd, False)
    os.set_inheritable(response_fd, False)
    module_name = f"__agentbox_kernel_{os.getpid()}__"
    module = types.ModuleType(module_name)
    module.__dict__["__builtins__"] = __builtins__
    sys.modules[module_name] = module
    namespace = module.__dict__

    with (
        os.fdopen(request_fd, "r", encoding="utf-8") as request_stream,
        os.fdopen(response_fd, "w", encoding="utf-8", buffering=1) as response_stream,
    ):
        for raw_request in request_stream:
            try:
                request = json.loads(raw_request)
                if not isinstance(request, dict):
                    raise ValueError("Runtime kernel request must be an object")
                response = _execute_request(request, namespace)
            except BaseException as exc:
                response = {
                    "ok": False,
                    "stdout": "",
                    "stderr": traceback.format_exc(),
                    "result": None,
                    "error_name": exc.__class__.__name__,
                    "cwd": os.getcwd(),
                }
            response_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            response_stream.flush()


@dataclass
class RuntimePythonKernel:
    """One stateful Python child, owned by exactly one runtime session."""

    process: subprocess.Popen[bytes]
    request_stream: TextIO
    response_stream: TextIO
    _io_lock: Lock = field(default_factory=Lock)

    @classmethod
    def start(cls) -> RuntimePythonKernel:
        kernel_path = Path(__file__).resolve()
        request_read_fd, request_write_fd = os.pipe()
        response_read_fd, response_write_fd = os.pipe()
        process: subprocess.Popen[bytes] | None = None
        request_stream: TextIO | None = None
        response_stream: TextIO | None = None
        try:
            child_env = os.environ.copy()
            child_env[_REQUEST_FD_ENV] = str(request_read_fd)
            child_env[_RESPONSE_FD_ENV] = str(response_write_fd)
            process = subprocess.Popen(
                [sys.executable, str(kernel_path), "--child"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_env,
                start_new_session=True,
                close_fds=True,
                pass_fds=(request_read_fd, response_write_fd),
            )
            os.close(request_read_fd)
            request_read_fd = -1
            os.close(response_write_fd)
            response_write_fd = -1
            request_stream = os.fdopen(
                request_write_fd,
                "w",
                encoding="utf-8",
                buffering=1,
            )
            request_write_fd = -1
            response_stream = os.fdopen(response_read_fd, "r", encoding="utf-8")
            response_read_fd = -1
            return cls(
                process=process,
                request_stream=request_stream,
                response_stream=response_stream,
            )
        except BaseException:
            if request_stream is not None:
                request_stream.close()
            if response_stream is not None:
                response_stream.close()
            for fd in (
                request_read_fd,
                request_write_fd,
                response_read_fd,
                response_write_fd,
            ):
                if fd >= 0:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            if process is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=_TERMINATION_GRACE_SECONDS)
            raise

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
            if not self.alive:
                raise RuntimeError("Python kernel is not running")
            try:
                self.request_stream.write(request + "\n")
                self.request_stream.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError(
                    "Python kernel stopped before accepting code"
                ) from exc

            line = _readline_with_timeout(self.response_stream, timeout_seconds)
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
        for stream in (self.request_stream, self.response_stream):
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
        request_fd = int(os.environ.pop(_REQUEST_FD_ENV))
        response_fd = int(os.environ.pop(_RESPONSE_FD_ENV))
        _kernel_main(request_fd, response_fd)
    else:
        raise SystemExit("runtime_kernel.py is an internal AgentBox child process")
