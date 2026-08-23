#!/usr/bin/env python3
"""Turn a scenario run's JUnit XML into a Slack message.

The point is that somebody reads it without opening CI. So it says what the
product does and does not do, in the product's own words — the scenario
sentences — rather than counting test ids.

Skips are reported as loudly as failures. A live lane whose credentials expired
goes green while testing nothing, and "0 failed" is exactly what that looks
like; the only defence is saying out loud what was not run.

Usage:

    python3 scripts/report_scenarios_to_slack.py results.xml --lane live \\
        --run-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"

Reads SLACK_WEBHOOK_URL from the environment. Without it, prints the message and
exits 0 — a missing webhook must not fail a build that otherwise passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

#: Enough detail to be useful, short enough that Slack does not collapse it.
#: Kept in step with `tests/scenarios/harness/consent.py`. Copied rather than
#: imported: this runs from the repo root in CI, without the suite on its path.
WAITING = "waiting on a person:"

MAX_LISTED = 8


@dataclass
class Outcome:
    passed: int = 0
    failed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def total(self) -> int:
        return self.passed + len(self.failed) + len(self.skipped)


def _name_of(case: ET.Element) -> str:
    """The scenario sentence, falling back to the test id.

    The reporting plugin writes `scenario` as a property on every case, which is
    the sentence a person would say. A case without one is either a guard or a
    test that forgot its decorator, and its id is the best available answer.
    """
    for properties in case.findall("properties"):
        for prop in properties.findall("property"):
            if prop.get("name") == "scenario" and prop.get("value"):
                return str(prop.get("value"))
    return case.get("name") or "(unnamed)"


def read(paths: list[Path]) -> Outcome:
    outcome = Outcome()
    for path in paths:
        if not path.is_file():
            continue
        for case in ET.parse(path).getroot().iter("testcase"):
            outcome.seconds += float(case.get("time") or 0)
            failure = case.find("failure") or case.find("error")
            skipped = case.find("skipped")
            if failure is not None:
                outcome.failed.append(_name_of(case))
            elif skipped is not None:
                reason = (skipped.get("message") or "").strip()
                outcome.skipped.append((_name_of(case), reason))
            else:
                outcome.passed += 1
    return outcome


def compose(outcome: Outcome, *, lane: str, run_url: str, ref: str) -> str:
    if outcome.total == 0:
        headline = f":warning: *{lane}* produced no results at all"
    elif outcome.failed:
        headline = (
            f":red_circle: *{lane}* — {len(outcome.failed)} of {outcome.total} "
            f"scenarios failing"
        )
    elif outcome.skipped:
        headline = (
            f":large_yellow_circle: *{lane}* — {outcome.passed} passing, "
            f"{len(outcome.skipped)} not run"
        )
    else:
        headline = f":large_green_circle: *{lane}* — all {outcome.passed} scenarios passing"

    lines = [headline, f"_{ref} · {outcome.seconds / 60:.0f}m_"]

    if outcome.failed:
        lines.append("\n*Not working:*")
        lines += [f"• {name}" for name in outcome.failed[:MAX_LISTED]]
        if len(outcome.failed) > MAX_LISTED:
            lines.append(f"• …and {len(outcome.failed) - MAX_LISTED} more")

    # Two different things wear the same pytest outcome. "Not applicable on this
    # deployment" is information; "somebody has to go and click something" is a
    # task, and burying it among the former is how a suite quietly proves less
    # each month. The lead comes from `harness/consent.py`.
    waiting = [pair for pair in outcome.skipped if pair[1].startswith(WAITING)]
    not_run = [pair for pair in outcome.skipped if not pair[1].startswith(WAITING)]

    for heading, group in (("*Not run:*", not_run), ("*Waiting on a person:*", waiting)):
        if not group:
            continue
        # Grouped by reason: twelve scenarios skipped for one expired token is
        # one problem, and listing it twelve times buries it.
        by_reason: dict[str, int] = {}
        for _name, reason in group:
            # The lead is what sorted it into this section; repeating it in
            # every bullet underneath is noise.
            said = reason[len(WAITING):] if reason.startswith(WAITING) else reason
            key = said.split(".")[0].strip() or "no reason given"
            by_reason[key] = by_reason.get(key, 0) + 1
        lines.append(f"\n{heading}")
        lines += [
            f"• {count}× {reason}"
            for reason, count in sorted(by_reason.items(), key=lambda item: -item[1])[
                :MAX_LISTED
            ]
        ]

    if run_url:
        lines.append(f"\n<{run_url}|Full run>")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--lane", default="scenarios")
    parser.add_argument("--run-url", default=os.getenv("SCENARIO_RUN_URL", ""))
    parser.add_argument("--ref", default=os.getenv("GITHUB_REF_NAME", "local"))
    args = parser.parse_args()

    outcome = read(args.results)
    message = compose(
        outcome, lane=args.lane, run_url=args.run_url, ref=args.ref
    )

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
        # Reporting is not the build. A Slack outage must not turn a green run
        # red, so this says so and stops.
        print(f"could not post to Slack: {error}", file=sys.stderr)
        print(message)
        return 0
    print(f"posted to Slack: {len(outcome.failed)} failing, {outcome.passed} passing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
