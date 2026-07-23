from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
from urllib.parse import urljoin
import zipfile

import httpx

from agentbox.observability import create_inherited_task

from .runtime_models import (
    AttemptClaim,
    FunctionArtifactManifest,
    RuntimeFailure,
    TerminalReport,
    WorkerRequest,
    WorkerResult,
)


_CACHE_ROOT = Path("/tmp/.lemma-function-cache")
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_LOG_BYTES = 4 * 1024 * 1024


class GatewayClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20, read=60), follow_redirects=False
        )

    async def claim(self, ticket: str) -> AttemptClaim:
        response = await self._client.post(
            urljoin(self._base_url, "internal/function-runtime/attempts:claim"),
            headers={"Authorization": f"Bearer {ticket}"},
            json={"runtime_abi": "lemma-function-python-1"},
        )
        response.raise_for_status()
        return AttemptClaim.model_validate(response.json())

    async def artifact(self, claim: AttemptClaim) -> bytes:
        response = await self._client.get(
            urljoin(self._base_url, claim.artifact_url.lstrip("/")),
            headers={"Authorization": f"Bearer {claim.runtime_token}"},
        )
        response.raise_for_status()
        if len(response.content) > _MAX_ARTIFACT_BYTES:
            raise ValueError("function artifact exceeds the runtime limit")
        return response.content

    async def started(self, claim: AttemptClaim) -> None:
        await self._post_event(claim, "started", {"fence": claim.fence})

    async def terminal(self, claim: AttemptClaim, report: TerminalReport) -> None:
        await self._post_event(claim, "terminal", report.model_dump(mode="json"))

    async def _post_event(
        self, claim: AttemptClaim, event: str, payload: dict[str, object]
    ) -> None:
        response = await self._client.post(
            urljoin(
                self._base_url,
                f"internal/function-runtime/attempts/{claim.attempt_id}:{event}",
            ),
            headers={"Authorization": f"Bearer {claim.runtime_token}"},
            json=payload,
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


def _ticket() -> str:
    ticket = sys.stdin.buffer.readline(8193).decode("utf-8").strip()
    if not ticket or len(ticket) > 8192:
        raise ValueError("a single-use attempt ticket is required on stdin")
    return ticket


def _verify_artifact(data: bytes, expected: str) -> str:
    actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if actual != expected:
        raise ValueError("function artifact digest mismatch")
    return actual.removeprefix("sha256:")


def _artifact_root(data: bytes, digest: str) -> Path:
    target = _CACHE_ROOT / digest
    ready = target / ".ready"
    if ready.is_file() and ready.read_text(encoding="utf-8") == digest:
        return target
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f"{digest}.", dir=_CACHE_ROOT))
    try:
        archive_path = temporary / "artifact.zip"
        archive_path.write_bytes(data)
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                destination = (temporary / member.filename).resolve()
                if temporary.resolve() not in destination.parents:
                    raise ValueError("function artifact contains an unsafe path")
                if member.file_size > _MAX_ARTIFACT_BYTES:
                    raise ValueError("function artifact member exceeds the limit")
            archive.extractall(temporary)
        archive_path.unlink()
        (temporary / ".ready").write_text(digest, encoding="utf-8")
        try:
            os.replace(temporary, target)
        except OSError:
            if not ready.is_file():
                raise
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


async def _bounded(stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    retained = bytearray()
    truncated = False
    while chunk := await stream.read(65536):
        remaining = _MAX_LOG_BYTES - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(retained), truncated


def _descendants(parent: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            values = dict(
                line.split(":", 1)
                for line in status.read_text().splitlines()
                if ":" in line
            )
            pid = int(values["Pid"].strip())
            ppid = int(values["PPid"].strip())
        except (OSError, KeyError, ValueError):
            continue
        children.setdefault(ppid, []).append(pid)
    result: list[int] = []
    pending = list(children.get(parent, ()))
    while pending:
        pid = pending.pop()
        result.append(pid)
        pending.extend(children.get(pid, ()))
    return result


async def _terminate_child(process: asyncio.subprocess.Process) -> None:
    for pid in reversed(_descendants(process.pid)):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        for pid in reversed(_descendants(process.pid)):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.returncode is None:
            process.kill()
        await process.wait()


async def _execute(claim: AttemptClaim, root: Path) -> TerminalReport:
    manifest = FunctionArtifactManifest.model_validate_json(
        (root / "manifest.json").read_bytes()
    )
    if manifest.runtime_abi != "lemma-function-python-1":
        raise ValueError("function artifact runtime ABI is incompatible")
    result_path = Path(f"/tmp/.lemma-function-result-{claim.attempt_id}.json")
    request = WorkerRequest(
        artifact_root=str(root),
        manifest=manifest,
        attempt_id=claim.attempt_id,
        input_data=claim.input_data,
        config=claim.config,
        identity=claim.identity,
        lemma_token=claim.lemma_token,
        lemma_base_url=claim.lemma_base_url,
    )
    env = os.environ.copy()
    env["LEMMA_FUNCTION_RESULT_PATH"] = str(result_path)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in ("/app", env.get("PYTHONPATH")) if part
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agentbox.function_runtime.worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert process.stdin is not None
    process.stdin.write(request.model_dump_json().encode())
    await process.stdin.drain()
    process.stdin.close()
    await process.stdin.wait_closed()
    stdout_task = create_inherited_task(_bounded(process.stdout))
    stderr_task = create_inherited_task(_bounded(process.stderr))
    try:
        remaining = claim.deadline_at.timestamp() - time.time()
        await asyncio.wait_for(process.wait(), timeout=max(0.01, remaining))
    except TimeoutError:
        await _terminate_child(process)
        raise
    stdout_result, stderr_result = await asyncio.gather(stdout_task, stderr_task)
    stdout, stdout_truncated = stdout_result
    stderr, stderr_truncated = stderr_result
    result = WorkerResult.model_validate_json(result_path.read_bytes())
    result_path.unlink(missing_ok=True)
    return TerminalReport(
        fence=claim.fence,
        status="completed" if result.ok else "failed",
        output_data=result.output_data,
        error=result.error,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
        output_truncated=stdout_truncated or stderr_truncated,
    )


async def run() -> int:
    ticket = _ticket()
    gateway_url = os.environ.get("LEMMA_FUNCTION_GATEWAY_URL")
    if not gateway_url:
        raise ValueError("LEMMA_FUNCTION_GATEWAY_URL is required")
    gateway = GatewayClient(gateway_url)
    claim: AttemptClaim | None = None
    try:
        claim = await gateway.claim(ticket)
        artifact = await gateway.artifact(claim)
        root = _artifact_root(
            artifact, _verify_artifact(artifact, claim.artifact_sha256)
        )
        await gateway.started(claim)
        try:
            report = await _execute(claim, root)
        except BaseException as exc:
            report = TerminalReport(
                fence=claim.fence,
                status="failed",
                error=RuntimeFailure(name=type(exc).__name__, message=str(exc)),
                stdout="",
                stderr="",
            )
        await gateway.terminal(claim, report)
        return 0
    finally:
        await gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="lemma-function-runtime")
    parser.add_argument("command", choices=("execute",))
    parser.parse_args()
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
