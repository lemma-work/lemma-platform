#!/usr/bin/env python3
"""Prove the two ingestion guarantees hold while the system is saturated.

``datastore_ingestion.py`` answers "how fast does a corpus index". This answers
the two questions that actually decide whether ingestion is production-ready:

1. **Lane isolation** — does a large upload burst slow down latency-sensitive
   work? Before lanes, document tasks parked on an in-task semaphore *while
   holding a worker slot*, so a bulk upload could starve agent runs. We measure
   interactive-path latency on a quiet baseline, then again during the burst,
   and compare.

2. **Per-pod fairness** — with two pods uploading at once, does the pod that
   submitted 10x more work monopolise ingestion? We interleave a "whale" pod and
   a "small" pod and check that the small pod's files complete without waiting
   for the whale to drain.

Both are pass/fail against explicit thresholds so this can gate a release rather
than just produce numbers to squint at.

Usage:
    uv run python load_tests/ingestion_isolation_probe.py \
        --api-url http://localhost:8000 --token "$LEMMA_TOKEN" \
        --whale-pod POD_A --small-pod POD_B --corpus ../benchmark-corpus/arxiv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

TERMINAL = {"COMPLETED", "FAILED", "FAILED_PERMANENT", "NOT_REQUIRED"}



def default_corpus_dir() -> Path:
    """Where the benchmark PDFs live.

    Kept outside the repo tree and gitignored (~310MB). ``LEMMA_BENCHMARK_CORPUS``
    wins if set; otherwise resolve ``benchmark-corpus/arxiv`` from the repo root,
    which in a git worktree is a symlink to the main checkout so every worktree
    shares one copy.
    """
    override = os.getenv("LEMMA_BENCHMARK_CORPUS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "benchmark-corpus" / "arxiv"


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


@dataclass
class Probe:
    """Latency samples for the interactive path."""

    label: str
    samples: list[float] = field(default_factory=list)
    errors: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "count": len(self.samples),
            "errors": self.errors,
            "p50_ms": round((_pct(self.samples, 0.50) or 0) * 1000, 1),
            "p95_ms": round((_pct(self.samples, 0.95) or 0) * 1000, 1),
            "max_ms": round((max(self.samples) if self.samples else 0) * 1000, 1),
        }


async def sample_interactive_latency(
    client: httpx.AsyncClient,
    pod_id: str,
    probe: Probe,
    stop: asyncio.Event,
    interval: float,
) -> None:
    """Poll a cheap authenticated read that shares the API + DB with ingestion.

    This is the signal a user actually feels. It must not degrade just because
    someone else is uploading a hundred documents.
    """
    while not stop.is_set():
        started = time.perf_counter()
        try:
            response = await client.get(f"/pods/{pod_id}/datastore/files/tree")
            response.raise_for_status()
            probe.samples.append(time.perf_counter() - started)
        except Exception:
            probe.errors += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def upload(
    client: httpx.AsyncClient,
    *,
    pod_id: str,
    source: Path,
    directory: str,
    name: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    content = await asyncio.to_thread(source.read_bytes)
    async with semaphore:
        response = await client.post(
            f"/pods/{pod_id}/datastore/files",
            data={
                "name": name,
                "directory_path": directory,
                "search_enabled": "true",
            },
            files={"data": (name, content, "application/pdf")},
        )
        response.raise_for_status()
        return {
            "path": f"{directory.rstrip('/')}/{name}",
            "submitted_at": time.perf_counter(),
        }


async def wait_for_completion(
    client: httpx.AsyncClient,
    *,
    pod_id: str,
    entries: list[dict[str, Any]],
    poll_interval: float,
    timeout: float,
    origin: float,
) -> list[dict[str, Any]]:
    """Poll until every entry reaches a terminal status, recording when."""
    pending = {entry["path"]: entry for entry in entries}
    done: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout

    while pending and time.monotonic() < deadline:
        for path in list(pending):
            try:
                response = await client.get(
                    f"/pods/{pod_id}/datastore/files/by-path", params={"path": path}
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue
            status = payload.get("status")
            if status in TERMINAL:
                entry = pending.pop(path)
                entry["status"] = status
                entry["attempts"] = int(payload.get("processing_attempts") or 0)
                entry["completed_at"] = time.perf_counter() - origin
                metrics = (payload.get("metadata") or {}).get("processing_metrics", {})
                entry["extraction_seconds"] = metrics.get("extraction_seconds")
                entry["chunk_count"] = metrics.get("chunk_count")
                entry["page_count"] = metrics.get("page_count")
                done.append(entry)
        if pending:
            await asyncio.sleep(poll_interval)

    for entry in pending.values():
        entry["status"] = "TIMEOUT"
        entry["completed_at"] = None
        done.append(entry)
    return done


async def run(args: argparse.Namespace) -> dict[str, Any]:
    corpus = sorted(Path(args.corpus or default_corpus_dir()).glob("*.pdf"))
    if not corpus:
        raise SystemExit(f"no PDFs found in {args.corpus}")

    headers = {"Authorization": f"Bearer {args.token}"}
    run_id = f"probe-{int(time.time())}"
    whale_dir = f"/isolation/{run_id}/whale"
    small_dir = f"/isolation/{run_id}/small"

    async with httpx.AsyncClient(
        base_url=args.api_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(args.request_timeout),
    ) as client:
        # --- 1. Quiet baseline for the interactive path -----------------------
        baseline = Probe("interactive_baseline")
        stop_baseline = asyncio.Event()
        baseline_task = asyncio.create_task(
            sample_interactive_latency(
                client, args.small_pod, baseline, stop_baseline, args.probe_interval
            )
        )
        await asyncio.sleep(args.baseline_seconds)
        stop_baseline.set()
        await baseline_task

        # --- 2. Burst: whale floods, small pod submits a few ------------------
        under_load = Probe("interactive_under_load")
        stop_load = asyncio.Event()
        load_task = asyncio.create_task(
            sample_interactive_latency(
                client, args.small_pod, under_load, stop_load, args.probe_interval
            )
        )

        origin = time.perf_counter()
        semaphore = asyncio.Semaphore(args.upload_concurrency)
        whale_sources = [corpus[i % len(corpus)] for i in range(args.whale_count)]
        small_sources = [corpus[i % len(corpus)] for i in range(args.small_count)]

        whale_entries = await asyncio.gather(
            *(
                upload(
                    client,
                    pod_id=args.whale_pod,
                    source=source,
                    directory=whale_dir,
                    name=f"whale-{index:03d}-{source.name}",
                    semaphore=semaphore,
                )
                for index, source in enumerate(whale_sources)
            )
        )
        # Submit the small pod's work AFTER the whale, which is the harsh case:
        # under plain FIFO these sit behind every whale file.
        small_entries = await asyncio.gather(
            *(
                upload(
                    client,
                    pod_id=args.small_pod,
                    source=source,
                    directory=small_dir,
                    name=f"small-{index:03d}-{source.name}",
                    semaphore=semaphore,
                )
                for index, source in enumerate(small_sources)
            )
        )

        whale_done, small_done = await asyncio.gather(
            wait_for_completion(
                client,
                pod_id=args.whale_pod,
                entries=whale_entries,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                origin=origin,
            ),
            wait_for_completion(
                client,
                pod_id=args.small_pod,
                entries=small_entries,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                origin=origin,
            ),
        )
        stop_load.set()
        await load_task

    return summarize(args, baseline, under_load, whale_done, small_done)


def summarize(
    args: argparse.Namespace,
    baseline: Probe,
    under_load: Probe,
    whale: list[dict[str, Any]],
    small: list[dict[str, Any]],
) -> dict[str, Any]:
    def completed(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e for e in entries if e.get("status") == "COMPLETED"]

    whale_ok, small_ok = completed(whale), completed(small)
    whale_times = [e["completed_at"] for e in whale_ok if e.get("completed_at")]
    small_times = [e["completed_at"] for e in small_ok if e.get("completed_at")]

    base_p95 = _pct(baseline.samples, 0.95) or 0.0
    load_p95 = _pct(under_load.samples, 0.95) or 0.0
    latency_ratio = (load_p95 / base_p95) if base_p95 > 0 else None

    # Fairness: the small pod submitted last and is far smaller, so under a fair
    # scheduler its median completion should land well before the whale fully
    # drains. Under strict FIFO it would land at/after the whale's tail.
    whale_last = max(whale_times) if whale_times else None
    small_median = statistics.median(small_times) if small_times else None
    fairness_ratio = (
        (small_median / whale_last) if (small_median and whale_last) else None
    )

    checks = {
        "interactive_latency_not_degraded": (
            latency_ratio is not None and latency_ratio <= args.max_latency_ratio
        ),
        "small_pod_not_starved": (
            fairness_ratio is not None and fairness_ratio <= args.max_fairness_ratio
        ),
        "no_permanent_failures": all(
            e.get("status") != "FAILED_PERMANENT" for e in (*whale, *small)
        ),
        "all_files_terminal": all(
            e.get("status") != "TIMEOUT" for e in (*whale, *small)
        ),
        "no_attempt_inflation": all(
            (e.get("attempts") or 0) <= args.max_attempts for e in (*whale, *small)
        ),
    }

    return {
        "config": {
            "whale_count": args.whale_count,
            "small_count": args.small_count,
            "upload_concurrency": args.upload_concurrency,
        },
        "interactive": {
            "baseline": baseline.summary(),
            "under_load": under_load.summary(),
            "p95_ratio": round(latency_ratio, 3) if latency_ratio else None,
            "threshold": args.max_latency_ratio,
        },
        "fairness": {
            "whale_completed": len(whale_ok),
            "whale_total": len(whale),
            "small_completed": len(small_ok),
            "small_total": len(small),
            "whale_drain_seconds": round(whale_last, 1) if whale_last else None,
            "small_median_seconds": round(small_median, 1) if small_median else None,
            "ratio": round(fairness_ratio, 3) if fairness_ratio else None,
            "threshold": args.max_fairness_ratio,
        },
        "failures": {
            "failed": sum(
                1 for e in (*whale, *small) if e.get("status", "").startswith("FAILED")
            ),
            "timeout": sum(1 for e in (*whale, *small) if e.get("status") == "TIMEOUT"),
            "max_attempts_seen": max(
                (e.get("attempts") or 0 for e in (*whale, *small)), default=0
            ),
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.getenv("LEMMA_API_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("LEMMA_TOKEN"))
    parser.add_argument("--whale-pod", required=True)
    parser.add_argument("--small-pod", required=True)
    parser.add_argument(
        "--corpus",
        default=None,
    )
    parser.add_argument("--whale-count", type=int, default=40)
    parser.add_argument("--small-count", type=int, default=4)
    parser.add_argument("--upload-concurrency", type=int, default=8)
    parser.add_argument("--baseline-seconds", type=float, default=20.0)
    parser.add_argument("--probe-interval", type=float, default=1.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--max-latency-ratio", type=float, default=3.0)
    parser.add_argument("--max-fairness-ratio", type=float, default=0.8)
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not args.token:
        parser.error("--token or LEMMA_TOKEN is required")

    report = asyncio.run(run(args))
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text)

    print("\n=== checks ===")
    for name, ok in report["checks"].items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
