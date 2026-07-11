"""Benchmark the real datastore upload -> extraction -> search-ready pipeline.

The script intentionally talks only to the public HTTP API. It uploads a
deterministic 20-document corpus (cycling the checked-in PDF fixtures), polls
each file to a terminal state, and writes per-file and aggregate timings as
JSON. Run against an isolated local stack; it creates real files and indexing
work in the selected pod.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "FAILED_PERMANENT", "NOT_REQUIRED"}
DEFAULT_FIXTURES = (
    Path(__file__).parents[1] / "app/modules/datastore/tests/fixtures/arxiv"
)


@dataclass(slots=True)
class FileResult:
    index: int
    source: str
    path: str
    file_id: str | None = None
    upload_seconds: float | None = None
    ready_seconds: float | None = None
    status: str = "UPLOAD_FAILED"
    processing_attempts: int = 0
    extraction_seconds: float | None = None
    projection_seconds: float | None = None
    indexing_seconds: float | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    projection_verified: bool = False
    content_sha256_verified: bool = False
    error: str | None = None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _timing_summary(values: list[float]) -> dict[str, float | None]:
    return {
        "mean_seconds": statistics.fmean(values) if values else None,
        "p50_seconds": _percentile(values, 0.50),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
    }


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def _corpus(fixtures_dir: Path, count: int) -> list[Path]:
    fixtures = sorted(fixtures_dir.glob("*.pdf"))
    if not fixtures:
        raise ValueError(f"No PDF fixtures found in {fixtures_dir}")
    return [fixtures[index % len(fixtures)] for index in range(count)]


async def _upload_and_wait(
    client: httpx.AsyncClient,
    *,
    pod_id: str,
    source: Path,
    index: int,
    directory_path: str,
    semaphore: asyncio.Semaphore,
    poll_interval: float,
    timeout: float,
) -> FileResult:
    benchmark_name = f"bench-{index + 1:02d}-{source.name}"
    public_path = f"{directory_path.rstrip('/')}/{benchmark_name}"
    result = FileResult(index=index + 1, source=source.name, path=public_path)
    started = time.perf_counter()

    try:
        content = await asyncio.to_thread(source.read_bytes)
        async with semaphore:
            upload_started = time.perf_counter()
            response = await client.post(
                f"/pods/{pod_id}/datastore/files",
                data={
                    "name": benchmark_name,
                    "directory_path": directory_path,
                    "search_enabled": "true",
                },
                files={"data": (benchmark_name, content, "application/pdf")},
            )
            response.raise_for_status()
            payload = response.json()
            result.file_id = str(payload["id"])
            result.content_sha256_verified = payload.get(
                "content_sha256"
            ) == hashlib.sha256(content).hexdigest()
            result.upload_seconds = time.perf_counter() - upload_started

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = await client.get(
                f"/pods/{pod_id}/datastore/files/by-path",
                params={"path": public_path},
            )
            response.raise_for_status()
            payload = response.json()
            result.status = payload["status"]
            result.processing_attempts = int(payload.get("processing_attempts") or 0)
            result.error = payload.get("last_processing_error")
            if result.status in TERMINAL_STATUSES:
                metrics = (payload.get("metadata") or {}).get("processing_metrics", {})
                result.extraction_seconds = metrics.get("extraction_seconds")
                result.projection_seconds = metrics.get("projection_seconds")
                result.indexing_seconds = metrics.get("indexing_seconds")
                result.page_count = metrics.get("page_count")
                result.chunk_count = metrics.get("chunk_count")
                if result.status == "COMPLETED":
                    children_response = await client.get(
                        f"/pods/{pod_id}/datastore/files/children",
                        params={"path": public_path},
                    )
                    children_response.raise_for_status()
                    child_names = {
                        item.get("name")
                        for item in children_response.json().get("items", [])
                    }
                    result.projection_verified = "document.md" in child_names
                    if not result.projection_verified:
                        result.status = "VALIDATION_FAILED"
                        result.error = "Completed PDF has no document.md projection"
                result.ready_seconds = time.perf_counter() - started
                return result
            await asyncio.sleep(poll_interval)

        result.status = "TIMEOUT"
        result.ready_seconds = time.perf_counter() - started
        result.error = f"Did not reach a terminal state within {timeout:.0f}s"
        return result
    except Exception as exc:
        result.ready_seconds = time.perf_counter() - started
        result.error = f"{type(exc).__name__}: {exc}"
        return result


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    corpus = _corpus(args.fixtures_dir, args.count)
    run_directory = f"{args.directory_path.rstrip('/')}/{args.run_id}"
    headers = {"Authorization": f"Bearer {args.token}"}
    semaphore = asyncio.Semaphore(args.upload_concurrency)
    wall_started = time.perf_counter()

    async with httpx.AsyncClient(
        base_url=args.api_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(args.request_timeout),
    ) as client:
        results = await asyncio.gather(
            *(
                _upload_and_wait(
                    client,
                    pod_id=args.pod_id,
                    source=source,
                    index=index,
                    directory_path=run_directory,
                    semaphore=semaphore,
                    poll_interval=args.poll_interval,
                    timeout=args.processing_timeout,
                )
                for index, source in enumerate(corpus)
            )
        )
        search_started = time.perf_counter()
        try:
            search_response = await client.post(
                f"/pods/{args.pod_id}/datastore/files/search",
                json={
                    "query": "attention",
                    "search_method": "HYBRID",
                    "scope_path": run_directory,
                    "scope_mode": "SUBTREE",
                    "limit": 10,
                },
            )
            search_response.raise_for_status()
            search_result_count = int(search_response.json().get("total") or 0)
            search_error = None
        except Exception as exc:
            search_result_count = 0
            search_error = f"{type(exc).__name__}: {exc}"
        search_seconds = time.perf_counter() - search_started

    wall_seconds = time.perf_counter() - wall_started
    completed_results = [item for item in results if item.status == "COMPLETED"]
    ready = [
        item.ready_seconds
        for item in completed_results
        if item.ready_seconds is not None
    ]
    completed = sum(item.status == "COMPLETED" for item in results)
    phase_timings = {
        name: _timing_summary(
            [
                value
                for item in completed_results
                if (value := getattr(item, attribute)) is not None
            ]
        )
        for name, attribute in (
            ("upload", "upload_seconds"),
            ("ready", "ready_seconds"),
            ("extraction", "extraction_seconds"),
            ("projection", "projection_seconds"),
            ("indexing", "indexing_seconds"),
        )
    }
    return {
        "configuration": {
            "api_url": args.api_url,
            "pod_id": args.pod_id,
            "count": args.count,
            "upload_concurrency": args.upload_concurrency,
            "fixtures_dir": str(args.fixtures_dir),
            "directory_path": run_directory,
            "run_id": args.run_id,
        },
        "summary": {
            "completed": completed,
            "failed": len(results) - completed,
            "success_rate": completed / len(results) if results else 0.0,
            "wall_seconds": wall_seconds,
            "documents_per_minute": (
                completed / wall_seconds * 60 if wall_seconds else 0.0
            ),
            "ready_mean_seconds": statistics.fmean(ready) if ready else None,
            "ready_p50_seconds": _percentile(ready, 0.50),
            "ready_p95_seconds": _percentile(ready, 0.95),
            "ready_max_seconds": max(ready) if ready else None,
            "timings": phase_timings,
            "projection_verified": sum(
                item.projection_verified for item in completed_results
            ),
            "content_sha256_verified": sum(
                item.content_sha256_verified for item in completed_results
            ),
            "search_verified": search_result_count > 0,
            "search_result_count": search_result_count,
            "search_seconds": search_seconds,
            "search_error": search_error,
        },
        "files": [asdict(item) for item in results],
    }


def main() -> None:
    _load_env_file(Path(__file__).with_name(".env.load_test"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url", default=os.getenv("LEMMA_API_URL", "http://localhost:8000")
    )
    parser.add_argument("--token", default=os.getenv("LEMMA_TOKEN"))
    parser.add_argument("--pod-id", default=os.getenv("LEMMA_POD_ID"))
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--upload-concurrency", type=int, default=5)
    parser.add_argument("--directory-path", default="/datastore-ingestion-benchmark")
    parser.add_argument(
        "--run-id",
        default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        help="Unique path component; defaults to the current UTC timestamp",
    )
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--processing-timeout", type=float, default=3600.0)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("/tmp/lemma-datastore-ingestion-report.json"),
    )
    args = parser.parse_args()
    if not args.token or not args.pod_id:
        parser.error("--token/LEMMA_TOKEN and --pod-id/LEMMA_POD_ID are required")
    if args.count < 1 or args.upload_concurrency < 1:
        parser.error("--count and --upload-concurrency must be positive")
    if not args.run_id or "/" in args.run_id:
        parser.error("--run-id must be a non-empty single path component")

    report = asyncio.run(_run(args))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Full report: {args.report}")
    if (
        report["summary"]["completed"] != args.count
        or report["summary"]["content_sha256_verified"] != args.count
        or not report["summary"]["search_verified"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
