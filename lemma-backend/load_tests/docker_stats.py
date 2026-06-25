"""Sample `docker stats` into a CSV during a load test.

Polls CPU% and memory for the running containers every --interval seconds and
appends rows to a CSV so the load-test run can be correlated with container
resource usage (alongside db_monitor.py for DB connections).

Captures ALL running containers each tick (the db/redis/supertokens services use
compose-generated names); filter by name when analysing. The api/worker use the
fixed names lemma-load-api / lemma-load-worker.

Usage:
    uv run python load_tests/docker_stats.py [--interval 2] [--output load_tests/docker_stats.csv]
"""

from __future__ import annotations

import argparse
import csv
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

_FORMAT = "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

_running = True


def _stop(_signum, _frame) -> None:
    global _running
    _running = False


def sample() -> list[tuple[str, str, str, str]]:
    """One `docker stats --no-stream` snapshot -> [(name, cpu%, mem_usage, mem%)]."""
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", _FORMAT],
        capture_output=True,
        text=True,
        check=False,
    )
    rows: list[tuple[str, str, str, str]] = []
    if result.returncode != 0:
        return rows
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--output", default="load_tests/docker_stats.csv")
    parser.add_argument(
        "--name-filter",
        default="",
        help="Optional substring; only rows whose container name contains it are kept.",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print(f"Sampling docker stats every {args.interval}s -> {args.output} (Ctrl+C to stop)")
    with open(args.output, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "name", "cpu_perc", "mem_usage", "mem_perc"])
        fh.flush()
        while _running:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for name, cpu, mem, mem_perc in sample():
                if args.name_filter and args.name_filter not in name:
                    continue
                writer.writerow([ts, name, cpu, mem, mem_perc])
            fh.flush()
            # sleep in small steps so Ctrl+C is responsive
            slept = 0.0
            while _running and slept < args.interval:
                time.sleep(min(0.2, args.interval - slept))
                slept += 0.2
    print("\nStopped docker stats sampling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
