from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import urljoin
from uuid import UUID
import zipfile

import httpx

from .runtime_models import (
    RunClaim,
    TerminalReport,
)
from .types import JsonObject


_CACHE_ROOT = Path(
    os.environ.get(
        "LEMMA_FUNCTION_CACHE_ROOT",
        "/tmp/.lemma-function-cache",
    )
)
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_LOG_BYTES = 4 * 1024 * 1024


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20, read=60),
            follow_redirects=False,
            transport=transport,
        )

    async def claim(
        self,
        function_token: str,
        *,
        run_id: UUID,
        revision_hash: str,
        input_data: JsonObject,
    ) -> RunClaim:
        response = await self._client.post(
            urljoin(
                self._base_url,
                f"internal/function-runtime/runs/{run_id}:claim",
            ),
            headers={"Authorization": f"Bearer {function_token}"},
            json={
                "revision_hash": revision_hash,
                "input_data": input_data,
            },
        )
        response.raise_for_status()
        return RunClaim.model_validate(response.json())

    async def artifact(self, claim: RunClaim) -> bytes:
        response = await self._client.get(
            urljoin(self._base_url, claim.artifact_url.lstrip("/")),
            headers={"Authorization": f"Bearer {claim.callback_token}"},
        )
        response.raise_for_status()
        if len(response.content) > _MAX_ARTIFACT_BYTES:
            raise ValueError("function artifact exceeds the runtime limit")
        return response.content

    async def terminal(self, claim: RunClaim, report: TerminalReport) -> None:
        await self._post_event(claim, "terminal", report.model_dump(mode="json"))

    async def _post_event(
        self, claim: RunClaim, event: str, payload: JsonObject
    ) -> None:
        url = urljoin(
            self._base_url,
            f"internal/function-runtime/runs/{claim.run_id}:{event}",
        )
        headers = {"Authorization": f"Bearer {claim.callback_token}"}
        retry_deadline = min(
            datetime.now(timezone.utc) + timedelta(seconds=5),
            claim.deadline_at.astimezone(timezone.utc) + timedelta(seconds=5),
        )
        delay_seconds = 0.1
        while True:
            remaining = (retry_deadline - datetime.now(timezone.utc)).total_seconds()
            request_timeout = max(0.1, min(2.0, remaining))
            retry_after_seconds: float | None = None
            try:
                response = await self._client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=request_timeout,
                )
            except httpx.TransportError:
                if datetime.now(timezone.utc) >= retry_deadline:
                    raise
            else:
                if 200 <= response.status_code < 300:
                    return
                if not self._retryable_event_status(response.status_code):
                    response.raise_for_status()
                retry_after_seconds = self._retry_after_seconds(response)
                if datetime.now(timezone.utc) >= retry_deadline:
                    response.raise_for_status()
            sleep_seconds = retry_after_seconds or delay_seconds
            remaining = (retry_deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise TimeoutError(f"{event} callback retry deadline elapsed")
            await asyncio.sleep(min(sleep_seconds, remaining))
            delay_seconds = min(delay_seconds * 2, 1.0)

    @staticmethod
    def _retryable_event_status(status_code: int) -> bool:
        return status_code in {408, 425, 429} or status_code >= 500

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        configured = response.headers.get("Retry-After")
        if configured is None:
            return None
        try:
            return max(0.05, min(float(configured), 1.0))
        except ValueError:
            return None

    async def close(self) -> None:
        await self._client.aclose()


def _verify_artifact(data: bytes, expected: str) -> str:
    actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if actual != expected:
        raise ValueError("function artifact digest mismatch")
    return actual.removeprefix("sha256:")


def _cached_artifact_root(digest: str) -> Path | None:
    target = _CACHE_ROOT / digest
    ready = target / ".ready"
    manifest = target / "manifest.json"
    try:
        valid = (
            target.is_dir()
            and manifest.is_file()
            and ready.read_text(encoding="utf-8") == digest
        )
    except OSError:
        return None
    return target if valid else None


@asynccontextmanager
async def _artifact_cache_lock(digest: str, *, deadline_at: datetime):
    """Serialize the first download of one immutable revision across processes."""

    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = _CACHE_ROOT / f".{digest}.lock"
    handle = lock_path.open("a+b")
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline_at.timestamp() - time.time()
                if remaining <= 0:
                    raise TimeoutError("artifact cache lock deadline elapsed")
                await asyncio.sleep(min(0.05, remaining))
        yield
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _artifact_root(data: bytes, digest: str) -> Path:
    target = _CACHE_ROOT / digest
    cached = _cached_artifact_root(digest)
    if cached is not None:
        return cached
    if target.exists():
        shutil.rmtree(target)
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
            cached = _cached_artifact_root(digest)
            if cached is None:
                raise
            return cached
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


async def _resolve_artifact_root(
    gateway: GatewayClient,
    claim: RunClaim,
) -> Path:
    """Resolve an immutable revision without re-fetching a warm artifact."""

    digest = claim.revision_hash.removeprefix("sha256:")
    cached = _cached_artifact_root(digest)
    if cached is not None:
        return cached
    async with _artifact_cache_lock(digest, deadline_at=claim.deadline_at):
        # Another invocation may have populated the cache while this process
        # waited for the revision lock.
        cached = _cached_artifact_root(digest)
        if cached is not None:
            return cached
        artifact = await gateway.artifact(claim)
        return _artifact_root(
            artifact,
            _verify_artifact(artifact, claim.revision_hash),
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="lemma-function-runtime")
    parser.add_argument("command", choices=("serve",))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--max-workers", type=int, default=32)
    parser.add_argument("--max-cached-revisions", type=int, default=16)
    args = parser.parse_args()
    import uvicorn

    from .server import create_app

    uvicorn.run(
        create_app(
            max_workers=args.max_workers,
            max_cached_revisions=args.max_cached_revisions,
        ),
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
