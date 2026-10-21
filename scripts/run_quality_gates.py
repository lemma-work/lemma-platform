#!/usr/bin/env python3
"""Run `make quality`'s gates side by side, and print each one whole.

Each gate is its own Make target (`QUALITY_GATES` in the root Makefile). They
are independent -- every one reads the tree and none writes what another reads
-- so running them one after another only spent wall-clock: 213 s of the CI
quality job, most of it waiting on whichever gate was collecting the backend
suite at the time.

`make -j` would run them in parallel too, but the macOS make (3.81) has no
`--output-sync`, so thirty gates' lines would interleave and a failure would be
unreadable. This buffers each gate's output and prints it in one block, in the
order the gates finish, then names every gate that failed.

Runs under the system `python3` (3.9 on macOS), like the other scripts
`quality` calls this way, so it keeps to syntax that interpreter parses.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple


def _run(gate: str) -> Tuple[str, int, str, float]:
    started = time.monotonic()
    result = subprocess.run(
        ["make", "--no-print-directory", gate],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return gate, result.returncode, result.stdout, time.monotonic() - started


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gates", nargs="+", help="Make targets, one per gate")
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 2,
        help="gates to run at once (default: CPU count; 1 is serial)",
    )
    args = parser.parse_args(argv)

    failed: List[str] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [pool.submit(_run, gate) for gate in args.gates]
        for future in as_completed(futures):
            gate, code, output, seconds = future.result()
            status = "ok" if code == 0 else f"FAILED (exit {code})"
            sys.stdout.write(output)
            if output and not output.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.write(f"   [{gate}: {status} in {seconds:.0f}s]\n")
            sys.stdout.flush()
            if code != 0:
                failed.append(gate)

    elapsed = time.monotonic() - started
    if failed:
        print(
            f"\n✗ {len(failed)} of {len(args.gates)} quality gates failed "
            f"in {elapsed:.0f}s: {', '.join(sorted(failed))}"
        )
        print("  Re-run one on its own with `make <gate>`.")
        return 1
    print(f"\n{len(args.gates)} quality gates passed in {elapsed:.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
