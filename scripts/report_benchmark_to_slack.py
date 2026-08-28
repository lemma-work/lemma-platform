#!/usr/bin/env python3
"""Turn a function-execution benchmark report into a Slack message.

The benchmark has been running nightly against Docker and weekly against E2B,
writing a JSON report into an artifact with a 30-day retention, and comparing it
to nothing. There is no baseline and no ratchet, so a sandbox that got twice as
slow would produce a green run and a file nobody opens.

A ratchet is the wrong instrument here anyway. These numbers move with the
runner, the provider and the day; a threshold tight enough to catch a real
regression would fire on a noisy Tuesday, and one loose enough not to would miss
a doubling. What actually works for a measurement like this is a person seeing
it regularly enough to know what normal looks like -- so this posts the numbers
every run, pass or fail, and lets the trend live in the channel.

Reports every run, not only failures, which is the opposite of the failure
notifier and deliberate: the interesting output of a benchmark is the number,
not the outcome.

Usage:

    python3 scripts/report_benchmark_to_slack.py \\
        .benchmark-results/function-execution/docker/*.json --lane docker \\
        --run-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"

Reads SLACK_WEBHOOK_URL from the environment. Without it, prints the message and
exits 0 -- reporting is not the build, and a missing webhook or a Slack outage
must not turn a green benchmark red.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: Short enough that Slack does not collapse it. The benchmark runs a handful of
#: cases; if it ever runs many more, the tail is what a person scrolls past.
MAX_CASES = 6


def newest(paths: list[Path]) -> Path | None:
    """The report to talk about.

    The workflow passes a glob. A run writes one file, named for the timestamp,
    but a re-run in the same workspace leaves the earlier one behind -- and
    reporting yesterday's numbers as today's is worse than reporting nothing.
    """
    reports = sorted(path for path in paths if path.is_file())
    return reports[-1] if reports else None


def _seconds(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.2f}s"


def _cold_start(report: dict) -> str:
    """The headline number for a sandbox: how long the first call takes.

    Taken from the `cold` samples rather than a case summary, because a case
    average folds the cold invocation in with warm ones and hides the figure
    that decides whether the lifecycle policy is right.
    """
    samples = [
        sample.get("terminal_seconds")
        for sample in report.get("cold") or []
        if isinstance(sample, dict)
    ]
    present = [value for value in samples if isinstance(value, (int, float))]
    if not present:
        return "not measured"
    return f"{max(present):.2f}s"


def compose(report: dict, *, lane: str, run_url: str, ref: str) -> str:
    cases = [case for case in report.get("cases") or [] if isinstance(case, dict)]
    errors = list(report.get("errors") or [])
    failed = sum(int(case.get("failed") or 0) for case in cases)

    if errors or failed:
        headline = (
            f":red_circle: *function benchmark · {lane}* — "
            f"{failed} failed invocation(s), {len(errors)} error(s)"
        )
    elif not cases:
        headline = f":warning: *function benchmark · {lane}* — the report has no cases"
    else:
        headline = f":large_green_circle: *function benchmark · {lane}*"

    lines = [
        headline,
        f"_{ref} · cold start {_cold_start(report)} · "
        f"concurrency {(report.get('config') or {}).get('concurrency', '?')}_",
    ]

    if cases:
        lines.append("")
        for case in cases[:MAX_CASES]:
            terminal = case.get("terminal") or {}
            overhead = case.get("platform_overhead") or {}
            lines.append(
                f"• *{case.get('case', '?')}* "
                f"p95 {_seconds(terminal.get('p95_seconds'))} · "
                f"mean {_seconds(terminal.get('mean_seconds'))} · "
                f"platform {_seconds(overhead.get('p95_seconds'))} · "
                f"{float(case.get('success_rate') or 0) * 100:.0f}% ok"
            )
        if len(cases) > MAX_CASES:
            lines.append(f"• …and {len(cases) - MAX_CASES} more cases")

    if errors:
        lines.append("\n*Errors:*")
        lines += [f"• {error}" for error in errors[:MAX_CASES]]
        if len(errors) > MAX_CASES:
            lines.append(f"• …and {len(errors) - MAX_CASES} more")

    if run_url:
        lines.append(f"\n<{run_url}|Full run>")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="*", type=Path)
    parser.add_argument("--lane", default="docker")
    parser.add_argument("--run-url", default=os.getenv("BENCHMARK_RUN_URL", ""))
    parser.add_argument("--ref", default=os.getenv("GITHUB_REF_NAME", "local"))
    args = parser.parse_args()

    path = newest(args.reports)
    if path is None:
        # The benchmark crashed before writing anything. The failure notifier
        # covers that; this must not add a second red on top of it.
        message = (
            f":red_circle: *function benchmark · {args.lane}* — "
            f"no report was written at all"
        )
    else:
        try:
            report = json.loads(path.read_text())
        except ValueError as error:
            print(f"could not read {path}: {error}", file=sys.stderr)
            return 0
        message = compose(report, lane=args.lane, run_url=args.run_url, ref=args.ref)

    webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook:
        print(message)
        print("\n(SLACK_WEBHOOK_URL is not set, so nothing was posted.)")
        return 0

    request = urllib.request.Request(
        webhook,
        data=json.dumps({"text": message}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"could not post to Slack: {error}", file=sys.stderr)
        print(message)
        return 0
    print(f"posted the {args.lane} benchmark to Slack")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
