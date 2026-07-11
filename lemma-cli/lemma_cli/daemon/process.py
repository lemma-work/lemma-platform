from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from pathlib import Path

# Fixes "ValueError: Separator is found, but chunk is longer than limit".
# Claude Code can output JSON lines exceeding the default 64 KB asyncio.StreamReader
# limit when tool results contain large file contents.
STREAM_READER_LIMIT = 10 * 1024 * 1024  # 10 MB


def resolve_executable(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    harness_kind: str,
) -> list[str]:
    """Resolve a provider binary against the child PATH before spawning it."""
    if not command:
        raise RuntimeError(f"No provider command configured for {harness_kind}")
    binary = command[0]
    lookup = binary
    if not os.path.isabs(binary) and os.path.dirname(binary):
        lookup = str(cwd / binary)
    executable = shutil.which(lookup, path=env.get("PATH"))
    if executable is None:
        raise FileNotFoundError(
            f"{harness_kind} executable '{command[0]}' was not found on PATH"
        )
    return [executable, *command[1:]]


async def create_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    harness_kind: str,
    stdin: bool = False,
) -> asyncio.subprocess.Process:
    """Create a subprocess with a 10 MB StreamReader limit on stdout/stderr."""
    resolved_command = resolve_executable(
        command,
        cwd=cwd,
        env=env,
        harness_kind=harness_kind,
    )
    return await asyncio.create_subprocess_exec(
        *resolved_command,
        stdin=asyncio.subprocess.PIPE if stdin else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        limit=STREAM_READER_LIMIT,
    )


async def drain_stream(stream: asyncio.StreamReader | None) -> str:
    if stream is None:
        return ""
    data = await stream.read()
    return data.decode(errors="replace")


async def terminate_gracefully(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
