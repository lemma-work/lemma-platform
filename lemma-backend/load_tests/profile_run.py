"""Run a load profile and collect everything needed to explain it, on one clock.

The pieces already existed and did not talk to each other. k6 emitted a latency
trend per API; `docker_stats.py` sampled container CPU and memory; `db_monitor.py`
polled `pg_stat_activity`; and `make load-test-run` took a single
`docker stats --no-stream` snapshot *after* the test, which cannot show a trend.
So you could see that p95 was bad and you could see that memory was high, and
nothing lined the two up.

What closes the gap is the third source: this release added a loop-stall sampler
that captures the stack of whatever is blocking the event loop, and a memory
sampler that reports a rising resident floor. Both write structured records to
the container log. Pulling those into the same directory, over the same window,
turns "p95 went to two seconds at minute four" into "p95 went to two seconds at
minute four and here is the frame that did it".

Usage:
    uv run python load_tests/profile_run.py --profile journey --users 20
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTAINERS = ("lemma-load-api", "lemma-load-worker")

# The three the samplers emit. `loop_stall` is the one that names a culprit.
_RUNTIME_EVENTS = (
    "runtime.loop_stall.degraded",
    "runtime.loop_lag.degraded",
    "runtime.memory.degraded",
    "http.request.slow",
)

_PROFILES = {
    "journey": "journey.js",
    "ws": "ws_concurrent.js",
    "sse": "sse_concurrent.js",
    "micro": "micro.js",
}


def _run_dir(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = ROOT / "runs" / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def _start_samplers(out: Path, interval: int) -> list[subprocess.Popen]:
    procs: list[subprocess.Popen] = []
    procs.append(
        subprocess.Popen(
            [
                sys.executable, str(ROOT / "docker_stats.py"),
                "--interval", str(interval),
                "--output", str(out / "docker_stats.csv"),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    )
    with (out / "db_connections.csv").open("w") as handle:
        procs.append(
            subprocess.Popen(
                [
                    sys.executable, str(ROOT / "db_monitor.py"),
                    "--interval", str(interval),
                ],
                stdout=handle, stderr=subprocess.DEVNULL,
            )
        )
    return procs


def _stop(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _run_k6(profile: str, out: Path, env: dict[str, str]) -> int:
    script = _PROFILES[profile]
    # `runs/` sits under load_tests, which is mounted at /scripts, so k6 can
    # write its summary straight into this run's directory.
    relative = (out / "k6-summary.json").relative_to(ROOT)
    args = [
        "docker", "run", "--rm", "--network", "host",
        "-v", f"{ROOT}:/scripts",
    ]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += [
        "grafana/k6:latest", "run",
        f"--summary-export=/scripts/{relative}",
        f"/scripts/{script}",
    ]
    with (out / "k6-stdout.txt").open("w") as handle:
        return subprocess.run(args, stdout=handle, stderr=subprocess.STDOUT).returncode


def _collect_runtime_events(out: Path, since: str) -> list[dict]:
    """Pull the samplers' structured records out of the container logs."""
    events: list[dict] = []
    for container in CONTAINERS:
        result = subprocess.run(
            ["docker", "logs", "--since", since, container],
            capture_output=True, text=True,
        )
        for line in (result.stdout + result.stderr).splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("event") in _RUNTIME_EVENTS:
                record["container"] = container
                events.append(record)
    (out / "runtime_events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events)
    )
    return events


def _memory_trend(out: Path) -> dict[str, dict[str, float]]:
    """Per-container memory floor over the run — the leak signal, not the peak."""
    path = out / "docker_stats.csv"
    if not path.exists():
        return {}
    series: dict[str, list[float]] = defaultdict(list)
    with path.open() as handle:
        for row in csv.DictReader(handle):
            name = row.get("name") or row.get("Name") or ""
            if name not in CONTAINERS:
                continue
            raw = (row.get("mem_usage") or row.get("MemUsage") or "").split("/")[0]
            match = re.match(r"([\d.]+)\s*([KMG])i?B", raw.strip(), re.I)
            if not match:
                continue
            value = float(match.group(1))
            unit = match.group(2).upper()
            series[name].append(value * {"K": 1 / 1024, "M": 1, "G": 1024}[unit])
    trend = {}
    for name, values in series.items():
        if not values:
            continue
        head = values[: max(1, len(values) // 4)]
        tail = values[-max(1, len(values) // 4) :]
        trend[name] = {
            "start_floor_mib": round(min(head), 1),
            "end_floor_mib": round(min(tail), 1),
            "peak_mib": round(max(values), 1),
            "samples": len(values),
        }
    return trend


def _stall_frames(events: list[dict]) -> Counter:
    """The innermost application frame of each captured stall."""
    frames: Counter = Counter()
    for event in events:
        if event.get("event") != "runtime.loop_stall.degraded":
            continue
        stack = event.get("stack_frames") or ""
        lines = [
            line.strip()
            for line in stack.splitlines()
            if line.strip().startswith("File")
        ]
        if lines:
            frames[re.sub(r'.*(app/|site-packages/)', "", lines[-1])] += 1
    return frames


def _write_summary(out: Path, events: list[dict], k6_rc: int) -> str:
    lines = [f"# Load profile — {out.name}", ""]

    summary_path = out / "k6-summary.json"
    if summary_path.exists():
        data = json.loads(summary_path.read_text())
        metrics = data.get("metrics", {})
        lines += ["## Latency by API (ms)", "", "| metric | p50 | p95 | p99 | max |", "|---|---|---|---|---|"]
        for name, values in sorted(metrics.items()):
            if not name.endswith("_ms") or "p(95)" not in values:
                continue
            lines.append(
                f"| {name} | {values.get('med', 0):.0f} | {values.get('p(95)', 0):.0f} "
                f"| {values.get('p(99)', 0):.0f} | {values.get('max', 0):.0f} |"
            )
        lines.append("")

    trend = _memory_trend(out)
    if trend:
        lines += ["## Memory floor", "", "| container | start | end | peak | samples |", "|---|---|---|---|---|"]
        for name, values in sorted(trend.items()):
            lines.append(
                f"| {name} | {values['start_floor_mib']} MiB | {values['end_floor_mib']} MiB "
                f"| {values['peak_mib']} MiB | {values['samples']} |"
            )
        lines += [
            "",
            "A floor that ends materially above where it started is the leak "
            "signal. A high peak with a flat floor is just work.",
            "",
        ]

    counts = Counter(event.get("event") for event in events)
    lines += ["## Runtime events", ""]
    if counts:
        for name, count in counts.most_common():
            lines.append(f"- `{name}` × {count}")
    else:
        lines.append("- none — no loop stalls, no lag, no memory growth reported")
    lines.append("")

    frames = _stall_frames(events)
    if frames:
        lines += ["## What blocked the loop", ""]
        for frame, count in frames.most_common(10):
            lines.append(f"- {count} × `{frame}`")
        lines.append("")

    slow = [e for e in events if e.get("event") == "http.request.slow"]
    if slow:
        by_route: dict[str, list[float]] = defaultdict(list)
        for event in slow:
            by_route[str(event.get("route"))].append(float(event.get("duration_ms") or 0))
        lines += ["## Slow requests", "", "| route | count | max ms |", "|---|---|---|"]
        for route, values in sorted(by_route.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"| {route} | {len(values)} | {max(values):.0f} |")
        lines.append("")

    lines.append(f"k6 exit code: {k6_rc}")
    text = "\n".join(lines)
    (out / "summary.md").write_text(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(_PROFILES), default="journey")
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--think-ms", type=int, default=1000)
    parser.add_argument("--interval", type=int, default=2)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("docker is not on PATH — start Docker and retry.", file=sys.stderr)
        return 1

    out = _run_dir(args.out)
    print(f"→ run directory: {out}")

    started = time.time()
    samplers = _start_samplers(out, args.interval)
    try:
        env = {
            "LEMMA_API_URL": args.api_url,
            "MAX_USERS": str(args.users),
            "THINK_MS": str(args.think_ms),
            "MAX_SUBSCRIBERS": str(args.users),
            "MAX_STREAMERS": str(args.users),
        }
        print(f"→ running k6 profile '{args.profile}' with {args.users} users…")
        k6_rc = _run_k6(args.profile, out, env)
    finally:
        _stop(samplers)

    since = datetime.fromtimestamp(started, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    events = _collect_runtime_events(out, since)
    print()
    print(_write_summary(out, events, k6_rc))
    return 0 if k6_rc == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
