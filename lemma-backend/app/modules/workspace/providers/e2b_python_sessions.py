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

from sandbox_runtime.protocol import (
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    PythonExecutionState,
    PythonSessionRef,
)

from app.modules.workspace.providers.base import ProviderInstance, PythonResult
from app.modules.workspace.providers.e2b_python_runner import PYTHON_RUNNER
from app.modules.workspace.providers.e2b_common import sdk_best_effort, sdk_errors
from app.modules.workspace.providers.e2b_process_lifetime import seconds_until


async def ensure_python_session(
    ops, instance: ProviderInstance, request: CreatePythonSessionRequest
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
    ops,
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
    sandbox = await ops._connect(instance.provider_id)
    state_path = f"/tmp/lemma-python-{session.session_id}.pkl"
    code_path = f"/tmp/lemma-python-{request.operation_id}.code"
    result_path = f"/tmp/lemma-python-{request.operation_id}.result"
    runner_path = f"/tmp/lemma-python-{request.operation_id}.py"

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
        with sdk_errors():
            await sandbox.files.write(code_path, request.code)
            await sandbox.files.write(
                runner_path,
                PYTHON_RUNNER.format(
                    state_path=state_path,
                    code_path=code_path,
                    result_path=result_path,
                ),
            )
            outcome = await sandbox.commands.run(
                f"python3 {runner_path}",
                cwd=session.cwd,
                envs={item.name: item.value for item in request.environment},
                # `None` here meant unbounded, so `execute_python`'s
                # `timeout_seconds` bounded only how long the backend
                # waited -- nothing stopped the code itself. A runaway loop
                # kept running in the sandbox after the tool had returned,
                # holding CPU and memory on a box with one core.
                timeout=seconds_until(request.deadline_at),
            )
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
        traceback=(),
        output_truncated=False,
    )


async def delete_python_session(
    ops, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
) -> None:
    sandbox = await ops._connect(instance.provider_id)
    # Nothing to forget is success.
    with sdk_best_effort():
        await sandbox.files.remove(f"/tmp/lemma-python-{session_id}.pkl")
