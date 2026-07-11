"""Reusable production streaq-worker subprocess for hermetic E2E journeys."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import redis.asyncio as redis

from app.modules.agent.tests.e2e.system_lemma_helpers import system_lemma_env_overlay

_DEFAULT_READINESS_MARKERS = (
    "Worker starting...",
    "`HandleAgentRunEvent` waiting for messages",
    "`HandleScheduleEvents` waiting for messages",
)


@dataclass(frozen=True)
class ProductionWorkerProcess:
    """Running production worker plus bounded diagnostics for E2E failures."""

    process: subprocess.Popen[str]
    log_path: Path

    def read_log_tail(self, *, max_characters: int = 200_000) -> str:
        content = _read_log(self.log_path)
        if len(content) <= max_characters:
            return content
        return "[earlier worker output omitted]\n" + content[-max_characters:]


def _read_log(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


@asynccontextmanager
async def production_worker_process(
    e2e_settings: Any,
    *,
    log_prefix: str,
    extra_env: Mapping[str, str] | None = None,
    readiness_markers: Sequence[str] = _DEFAULT_READINESS_MARKERS,
    startup_attempts: int = 600,
    worker_entrypoint: str = "app.events:streaq_worker",
) -> AsyncIterator[ProductionWorkerProcess]:
    """Start the same worker entrypoint used in production with hermetic I/O.

    Module suites provide only their fake-provider URLs and adapter selection.
    Database, Redis, local storage, coverage, and deterministic model settings
    remain centralized here so workers cannot silently diverge between shards.
    """
    redis_client = redis.from_url(e2e_settings.redis_url, decode_responses=False)
    await redis_client.flushdb()
    await redis_client.aclose()

    backend_root = Path(__file__).resolve().parents[4]
    log_path = Path(f"/tmp/{log_prefix}_{uuid4().hex}.log")
    coverage_env = {
        name: value
        for name in ("COVERAGE_PROCESS_START", "COVERAGE_FILE")
        if (value := os.environ.get(name))
    }
    environment = {
        **os.environ,
        **system_lemma_env_overlay(),
        **coverage_env,
        "PYTHONPATH": ".",
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": e2e_settings.database_url,
        "DATASTORE_DATABASE_URL": e2e_settings.datastore_database_url,
        "REDIS_URL": e2e_settings.redis_url,
        "API_URL": os.environ.get("API_URL", e2e_settings.api_url),
        "AGENTBOX_API_URL": e2e_settings.agentbox_api_url,
        "AGENTBOX_API_KEY": e2e_settings.agentbox_api_key,
        "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
        "ENVIRONMENT": "testing",
        "DEBUG": "true",
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": e2e_settings.email_output_dir,
        "GCS_STORAGE_BUCKET": "",
        "STORAGE_BUCKET": "",
        "PUBLIC_BUCKET_NAME": "",
        "STORAGE_BACKEND": "local",
        "EMBEDDING_PROVIDER": "local",
        "LOCAL_OBJECT_STORAGE_ROOT": e2e_settings.local_object_storage_root,
        "LOCAL_FILE_STORAGE_ROOT": e2e_settings.local_file_storage_root,
        "COMPOSIO_CACHE_DIR": "/tmp/composio",
        **(extra_env or {}),
    }

    with log_path.open("w") as log_writer:
        process = subprocess.Popen(
            [
                str(backend_root / ".venv/bin/streaq"),
                "run",
                worker_entrypoint,
            ],
            cwd=str(backend_root),
            env=environment,
            stdout=log_writer,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(startup_attempts):
            if process.poll() is not None:
                pytest.fail(
                    f"{log_prefix} worker exited before startup "
                    f"(code={process.returncode}).\n{_read_log(log_path)}"
                )
            if all(marker in _read_log(log_path) for marker in readiness_markers):
                break
            await asyncio.sleep(0.1)
        else:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            pytest.fail(
                f"Timed out waiting for {log_prefix} worker.\n{_read_log(log_path)}"
            )

        try:
            yield ProductionWorkerProcess(process=process, log_path=log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            redis_client = redis.from_url(
                e2e_settings.redis_url, decode_responses=False
            )
            await redis_client.flushdb()
            await redis_client.aclose()
