#!/usr/bin/env python3
"""Turn a scenario run's JUnit XML into something a person reads.

The point is that somebody reads it without opening CI. So it says what the
product does and does not do, in the product's own words — the scenario
sentences — rather than counting test ids.

Two sinks, one reading of the same XML, because the audiences differ and the
facts must not. Slack is for the nightly, where nobody is watching. A pull
request comment is for a labelled `run-scenarios` run, where somebody is: the
same shape of report the backend coverage gate already posts, so a reviewer
learns one place to look.

Grouped by journey, which is a property the reporting plugin writes onto every
case. Grouping by lane instead would name the CI shard, and a shard is an
artifact of how the work was split rather than anything about the product.

Skips are reported as loudly as failures. A live lane whose credentials expired
goes green while testing nothing, and "0 failed" is exactly what that looks
like; the only defence is saying out loud what was not run.

Usage:

    python3 scripts/report_scenarios_to_slack.py 'results/**/*.xml' \\
        --lane nightly --markdown-out summary.md \\
        --run-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"

Reads SLACK_WEBHOOK_URL from the environment. Without it, prints the message and
exits 0 — a missing webhook must not fail a build that otherwise passed, and
neither must a Slack outage.
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

#: Identifies the one comment this posts, so a re-run edits it rather than
#: adding another. Same mechanism as the backend coverage gate.
COMMENT_MARKER = "<!-- lemma-product-scenarios -->"


@dataclass
class Outcome:
    passed: int = 0
    failed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def total(self) -> int:
        return self.passed + len(self.failed) + len(self.skipped)


def _duration(seconds: float) -> str:
    """Minutes once there are minutes, seconds before that.

    "0 minutes of scenarios" is what a rounded figure says about a fast run,
    and it reads as a broken report rather than a quick one.
    """
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f}m"


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


def _property(case: ET.Element, name: str) -> str | None:
    for properties in case.findall("properties"):
        for prop in properties.findall("property"):
            if prop.get("name") == name and prop.get("value"):
                return str(prop.get("value"))
    return None


def _journey_of(case: ET.Element) -> str:
    """The stretch of product a case belongs to.

    Written as a JUnit property by `harness/reporting.py`. A case without one is
    a guard or a test that forgot its decorator; those are worth reporting, just
    not worth inventing a journey for.
    """
    return _property(case, "journey") or "(uncategorised)"


def read(paths: list[Path]) -> dict[str, Outcome]:
    """Every case in every report, grouped by journey.

    Deliberately not grouped by the file it came from. The files are CI shards,
    and a shard is a fact about how the work was split; a journey is a fact
    about the product, and the whole point of this suite is to report the
    second.
    """
    journeys: dict[str, Outcome] = {}
    for path in paths:
        if not path.is_file():
            continue
        for case in ET.parse(path).getroot().iter("testcase"):
            outcome = journeys.setdefault(_journey_of(case), Outcome())
            outcome.seconds += float(case.get("time") or 0)
            # `is not None`, not `or`. An ElementTree element with no children
            # is falsy, and a JUnit <failure> almost never has children -- so
            # `find("failure") or find("error")` evaluated the failure as
            # false, fell through to `error`, found nothing, and counted a
            # broken scenario as a passing one. This reporter exists to say
            # what stopped working; it was saying the opposite.
            failure = case.find("failure")
            if failure is None:
                failure = case.find("error")
            skipped = case.find("skipped")
            if failure is not None:
                outcome.failed.append(_name_of(case))
            elif skipped is not None:
                reason = (skipped.get("message") or "").strip()
                outcome.skipped.append((_name_of(case), reason))
            else:
                outcome.passed += 1
    return journeys


def combine(journeys: dict[str, Outcome]) -> Outcome:
    total = Outcome()
    for outcome in journeys.values():
        total.passed += outcome.passed
        total.failed += outcome.failed
        total.skipped += outcome.skipped
        total.seconds += outcome.seconds
    return total


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

    lines = [headline, f"_{ref} · {_duration(outcome.seconds)}_"]

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


def compose_markdown(
    journeys: dict[str, Outcome], *, lane: str, run_url: str
) -> str:
    """The pull-request comment.

    A table of journeys rather than the flat list Slack gets: a reviewer is
    deciding whether *their change* broke something, and "surfaces and
    notifications is red, the other ten are green" answers that in one glance
    where a list of thirty sentences does not.
    """
    total = combine(journeys)
    marker = COMMENT_MARKER
    if total.total == 0:
        return (
            f"{marker}\n### Product scenarios\n\n"
            f":warning: The `{lane}` run produced no results at all.\n"
        )

    if total.failed:
        broken = len(total.failed)
        verdict = (
            f":red_circle: **{broken} of {total.total} "
            f"{'scenario is' if broken == 1 else 'scenarios are'} not working.**"
        )
    elif total.skipped:
        verdict = (
            f":large_yellow_circle: **{total.passed} passing, "
            f"{len(total.skipped)} not run.**"
        )
    else:
        verdict = f":large_green_circle: **All {total.passed} scenarios passing.**"

    lines = [
        marker,
        "### Product scenarios",
        "",
        verdict,
        "",
        "| Journey | Working | Not working | Not run |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name in sorted(journeys):
        outcome = journeys[name]
        # A zero is noise in a table somebody scans for problems; an em dash is
        # read as "nothing to see" without being read at all.
        broken = str(len(outcome.failed)) if outcome.failed else "—"
        unrun = str(len(outcome.skipped)) if outcome.skipped else "—"
        lines.append(f"| {name} | {outcome.passed} | {broken} | {unrun} |")

    if total.failed:
        lines += ["", "<details><summary>What stopped working</summary>", ""]
        lines += [f"- {name}" for name in total.failed]
        lines += ["", "</details>"]

    # Same distinction the Slack message draws, for the same reason: "not
    # applicable here" is information and "somebody has to go and click
    # something" is a task, and one buried in the other is how a suite quietly
    # proves less each month.
    waiting = [pair for pair in total.skipped if pair[1].startswith(WAITING)]
    if waiting:
        lines += ["", "<details><summary>Waiting on a person</summary>", ""]
        lines += [
            f"- {name} — {reason[len(WAITING):].strip()}" for name, reason in waiting
        ]
        lines += ["", "</details>"]

    lines += ["", f"_{_duration(total.seconds)} of scenarios"]
    lines[-1] += f" · [full run]({run_url})_" if run_url else "_"
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--lane", default="scenarios")
    parser.add_argument("--run-url", default=os.getenv("SCENARIO_RUN_URL", ""))
    parser.add_argument("--ref", default=os.getenv("GITHUB_REF_NAME", "local"))
    parser.add_argument(
        "--markdown-out",
        type=Path,
        help="Write the pull-request comment here instead of posting to Slack.",
    )
    args = parser.parse_args()

    journeys = read(args.results)
    total = combine(journeys)

    # One sink per invocation. A pull request already has somebody reading it,
    # so posting the same run to Slack as well would be the duplicate noise the
    # failure notifier is careful to avoid.
    if args.markdown_out is not None:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(
            compose_markdown(journeys, lane=args.lane, run_url=args.run_url),
            encoding="utf-8",
        )
        print(
            f"wrote {args.markdown_out}: {len(total.failed)} failing, "
            f"{total.passed} passing, across {len(journeys)} journeys"
        )
        return 0

    message = compose(total, lane=args.lane, run_url=args.run_url, ref=args.ref)

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
    print(f"posted to Slack: {len(total.failed)} failing, {total.passed} passing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
