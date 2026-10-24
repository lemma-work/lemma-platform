"""Python sessions on E2B, which are a file rather than an interpreter.

Its own module because `e2b_ops` was at the size limit and this half has a
different shape from the rest of it: the runtime-backed fabrics keep a resident
interpreter per session, and here a session is a pickle on disk that each
execution loads and rewrites. What survives between calls is therefore whatever
pickles, which is the one thing about this surface a caller has to know.

Free functions taking the ops object rather than a mixin: they need its sandbox
connection and output buffer, and a fourth base class on the provider buys
nothing that an argument does not.
"""

from __future__ import annotations

from datetime import datetime
import shlex
from typing import Protocol

from sandbox_runtime.paths import LEMMA_PYTHON, SESSION_ROOT, USER_SITE_PACKAGES
from sandbox_runtime.protocol import (
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    PythonExecutionState,
    PythonSessionRef,
)

from app.modules.workspace.contracts.session_token_file import TOKEN_FILE_SHELL_PREFIX
from app.modules.workspace.providers.base import ProviderInstance, PythonResult
from app.modules.workspace.providers.e2b_python_runner import PYTHON_RUNNER
from app.modules.workspace.providers.e2b_common import (
    CommandExited,
    sdk_best_effort,
    sdk_errors,
)
from app.modules.workspace.providers.e2b_output import E2BOutputBuffer
from app.modules.workspace.providers.e2b_process_lifetime import seconds_until


#: What the per-session key is derived for, so it is never another signature.
_STATE_KEY_PURPOSE = "workspace.e2b-python-state"


def python_session_dir(session_id: object) -> str:
    """This Python session's own directory: its saved namespace and each call's
    files. ``0700``, made so by the command that runs the call.

    It was ``/tmp/lemma-python-<session>.pkl``: a name anyone could work out, in
    a directory anyone can write, holding a pickle the next call unpickled --
    which is to say ran.
    """
    return f"{SESSION_ROOT}/python-{session_id}"


def python_state_key(sandbox_id: str, session_id: object) -> str:
    """The key this session's saved namespace is sealed with.

    Derived rather than stored: any backend process computes the same one from
    the signing keyring, and nothing in the sandbox can, so a state file that
    verifies was written by a call the backend made for this session. Rotating
    the signing key changes it, and a session then starts once with an empty
    namespace -- what a recreated sandbox does anyway.
    """
    from app.core.crypto.factory import get_secret_signer

    return get_secret_signer().sign(
        _STATE_KEY_PURPOSE, f"{sandbox_id}:{session_id}".encode()
    )


def python_command(session_dir: str, runner_path: str) -> str:
    """The shell command one call runs.

    The image's interpreter by absolute path and isolated (``-I``), never
    ``python3`` from ``PATH``: that starts with the home's ``~/.python/bin``, and
    a script run by path puts its own directory -- here, before this, ``/tmp`` --
    first on ``sys.path``. A template from before ``/opt/lemma-python`` falls
    back to the system interpreter, still isolated.

    The session's token file is written first, as for any other command: the
    call's environment carries the session's newest token.
    """
    directory = shlex.quote(session_dir)
    return TOKEN_FILE_SHELL_PREFIX + (
        f"umask 077 && /bin/mkdir -p {directory} && /bin/chmod 700 {directory} && "
        f"if [ -x {LEMMA_PYTHON} ]; then _py={LEMMA_PYTHON}; "
        "else _py=/usr/bin/python3; fi && "
        f'exec "$_py" -I {shlex.quote(runner_path)}'
    )


class SessionHost(Protocol):
    """What these functions need of the E2B provider, and nothing else.

    Named so the provider is checked against it at the call sites: an untyped
    ``ops`` would accept anything and fail on the first missing attribute.

    The connected sandbox is passed separately, by the provider that owns the
    connection. It is the E2B SDK's object, and that SDK is an optional
    dependency, so it stays unannotated rather than pretending to a type the
    checker may not be able to import.
    """

    _output: E2BOutputBuffer

    async def _remember_pid(
        self,
        process_id: str,
        pid: int,
        *,
        tty: bool,
        sandbox_id: str = "",
        expires_at: float = 0.0,
        cwd: str = "",
        command: str = "",
    ) -> None: ...


async def ensure_python_session(
    ops: SessionHost, instance: ProviderInstance, request: CreatePythonSessionRequest
) -> None:
    """No-op: a session is a file on disk, created on first execution.

    E2B has no resident-interpreter concept to reserve, so there is nothing
    to allocate ahead of time and pretending otherwise would mean tracking
    state with no backing.

    The request's ``cwd`` is deliberately not remembered here either. It
    arrives again on every ``execute_python`` through the session reference,
    which is the only form of it that survives this backend running in more
    than one process -- remembering it in this one would work until the next
    call landed on another worker.
    """
    return


async def execute_python(
    ops: SessionHost,
    sandbox,
    instance: ProviderInstance,
    session: PythonSessionRef,
    request: ExecutePythonRequest,
) -> PythonResult:
    """Run code with REPL semantics and session continuity.

    The workspace image keeps a real interpreter per session. E2B's plain
    sandbox does not, so both properties are rebuilt here from what it does
    offer, and neither is faked:

    *Continuity* -- each execution restores the session's namespace from
    disk and saves it back, so a name bound in one call is available in the
    next. Only picklable values survive, which is the honest limit: an open
    file handle cannot cross a process boundary, and pretending it did
    would be worse than losing it.

    *A result* -- a REPL reports the value of a trailing expression, so the
    code is split with `ast` and the last node evaluated separately when it
    is an expression. Without this, `x = 6 * 7` followed by `x` returns
    nothing and an agent cannot see what it computed.

    *A working directory* -- the interpreter starts in the session's `cwd`,
    the very same one `start_process` gives a shell command. A fresh process
    per call means there is no shell to inherit it from, and without this it
    started in whatever directory the image defaults to: `execute_python`
    reported `/workspace` while `exec_command` reported the conversation's
    own directory, so a file one tool wrote by relative path was invisible
    to the other.
    """
    session_dir = python_session_dir(session.session_id)
    state_path = f"{session_dir}/state.pkl"
    code_path = f"{session_dir}/{request.operation_id}.code"
    result_path = f"{session_dir}/{request.operation_id}.result"
    runner_path = f"{session_dir}/{request.operation_id}.py"

    # Registered like any other process, because the idle sweep decides
    # what to release from this index and nothing put `execute_python` in
    # it. The comment below reasoned that a runaway loop would be held by
    # "the idle sweeper will not release a sandbox with live processes" --
    # true of `exec_command`, and never true of this call, so a ten-minute
    # analysis had no protection at all. The pid is not recorded: this run
    # is blocking, so nothing addresses it by pid.
    tracked_id = str(request.operation_id)
    await ops._remember_pid(
        tracked_id,
        0,
        tty=False,
        sandbox_id=instance.provider_id,
        expires_at=request.deadline_at.timestamp(),
        cwd=session.cwd or "",
        command="execute_python",
    )
    await ops._output.record_start(tracked_id)
    exit_code: int | None = None
    try:
        try:
            with sdk_errors():
                await sandbox.files.write(code_path, request.code)
                await sandbox.files.write(
                    runner_path,
                    PYTHON_RUNNER.format(
                        key=python_state_key(instance.provider_id, session.session_id),
                        state_path=state_path,
                        code_path=code_path,
                        result_path=result_path,
                        user_site=USER_SITE_PACKAGES,
                    ),
                )
                outcome = await sandbox.commands.run(
                    python_command(session_dir, runner_path),
                    cwd=session.cwd,
                    envs={item.name: item.value for item in request.environment},
                    # `None` here meant unbounded, so `execute_python`'s
                    # `timeout_seconds` bounded only how long the backend
                    # waited -- nothing stopped the code itself. A runaway loop
                    # kept running in the sandbox after the tool had returned,
                    # holding CPU and memory on a box with one core.
                    timeout=seconds_until(request.deadline_at),
                )
        except CommandExited as exited:
            # The agent's code raised. That is this call's answer -- a FAILED
            # result with the traceback -- not a sandbox that went away.
            outcome = exited.result
        exit_code = outcome.exit_code
    finally:
        if exit_code is None:
            await ops._output.record_unknown(tracked_id)
        else:
            await ops._output.record_exit(tracked_id, exit_code=exit_code)

    # No trailing expression, or a run that failed before writing one,
    # both mean there is no result to report.
    result: str | None = None
    with sdk_best_effort(result_path):
        raw = await sandbox.files.read(result_path, format="text")
        result = raw if raw else None

    failed = outcome.exit_code != 0
    return PythonResult(
        operation_id=request.operation_id,
        state=(
            PythonExecutionState.FAILED if failed else PythonExecutionState.SUCCEEDED
        ),
        stdout=outcome.stdout or "",
        stderr=outcome.stderr or "",
        result=result,
        error_name="ExecutionError" if failed else None,
        error_message=(outcome.stderr or None) if failed else None,
        traceback=None,
        output_truncated=False,
    )


async def delete_python_session(
    sandbox,
    *,
    session_id: str,
    deadline_at: datetime,
) -> None:
    # Nothing to forget is success.
    with sdk_best_effort():
        await sandbox.files.remove(python_session_dir(session_id))
    # Where a session's state lived before it had a directory of its own.
    for legacy in (
        f"/tmp/lemma-python-{session_id}.pkl",
        f"/tmp/lemma-python-{session_id}.pkl.modules",
    ):
        with sdk_best_effort():
            await sandbox.files.remove(legacy)
