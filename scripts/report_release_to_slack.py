#!/usr/bin/env python3
"""Announce a release in Slack, and say what is actually installable.

Not a "we shipped" message. The interesting half is the second one: it asks the
indexes what they are serving and prints that beside the version being
announced.

This exists because of 0.7.2. The GitHub Release was published on 2026-09-01 and
looked exactly like a release; PyPI and npm went on serving 0.7.1 for ten days,
because the release was created by a workflow and GitHub will not start a new
workflow run from an event the default GITHUB_TOKEN raised, so nothing that
publishes a package ever ran. Every dashboard said the release had happened. An
announcement that repeated that claim would have made it worse -- it would have
told the team a version was available that they could not install.

So a component that has not landed is named as missing, and the message says so
in its first line. The announcement is the check.

The prose comes from the changelog entry the release already has, in
``lemma-frontend/content/changelog/``, rather than from generated commit notes:
it is the one description of the release written for a person to read.

Usage::

    python3 scripts/report_release_to_slack.py 0.8.0
    python3 scripts/report_release_to_slack.py v0.8.0 --dry-run

Reads ``SLACK_WEBHOOK_URL`` from the environment. Without it the message is
printed and the script exits 0 -- a missing webhook must not fail a release that
otherwise succeeded, and neither must a Slack outage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_DIR = REPO_ROOT / "lemma-frontend/content/changelog"
REPOSITORY = "lemma-work/lemma-platform"

#: Slack renders a `text` payload well past this, but stops being read long
#: before it. The headings carry the shape of the release; the changelog carries
#: the rest, and it is one click away.
MAX_HEADINGS = 12

NETWORK_TIMEOUT = 20

#: How often to re-ask the indexes while waiting for a release to finish
#: landing. They are third-party and cached; asking faster buys nothing.
POLL_SECONDS = 30


#: Returned for a 404, which is an answer rather than a failure to get one.
ABSENT = object()


def _read(url: str):
    """The JSON at ``url``, ``ABSENT`` for a 404, or None when unknown.

    Never raises. A registry being slow or unreachable must not turn a
    successful release into a failed announcement -- the message says "could not
    check" and a person looks, which is strictly better than the script dying
    and nobody hearing anything at all.

    A 404 is separated out because it is not that kind of failure. "No release
    is tagged v0.8.0" and "GitHub did not answer" read the same to a caller that
    collapses both to None, and they mean opposite things: the first is the
    missing-component case this script exists to shout about, and reporting it
    as "could not check" is how it would go quiet at exactly the wrong moment.
    """
    try:
        with urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return ABSENT if error.code == 404 else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def pypi_has(package: str, version: str) -> Optional[bool]:
    """Whether PyPI serves ``version`` of ``package``. None when unknown."""
    data = _read("https://pypi.org/pypi/{}/json".format(package))
    if data is ABSENT:
        return False
    if data is None:
        return None
    return version in (data.get("releases") or {})


def npm_has(package: str, version: str) -> Optional[bool]:
    """Whether npm serves ``version`` of ``package``. None when unknown."""
    data = _read("https://registry.npmjs.org/{}".format(package))
    if data is ABSENT:
        return False
    if data is None:
        return None
    return version in (data.get("versions") or {})


def github_release_exists(version: str) -> Optional[bool]:
    """Whether a published GitHub Release names this version. None when unknown."""
    data = _read(
        "https://api.github.com/repos/{}/releases/tags/v{}".format(REPOSITORY, version)
    )
    if data is ABSENT:
        return False
    if data is None:
        return None
    return bool(data.get("tag_name"))


def changelog_path(version: str) -> Path:
    """The entry for this version. Named with dashes: 0.8.0 -> 0-8-0.mdx."""
    return CHANGELOG_DIR / "{}.mdx".format(version.replace(".", "-"))


def split_frontmatter(text: str) -> Tuple[dict, str]:
    """The entry's frontmatter fields and its body.

    A deliberately small reader rather than a YAML dependency: this runs from
    the repo root in CI with nothing installed, the same way the other reporting
    scripts here do. It reads the scalar fields it needs and ignores the rest,
    so a list value like `tags:` costs nothing.
    """
    if not text.startswith("---"):
        return {}, text
    closing = text.find("\n---", 3)
    if closing == -1:
        return {}, text
    fields = {}
    for line in text[3:closing].splitlines():
        match = re.match(r"^([A-Za-z_]+):\s*(.+?)\s*$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip("\"'")
    return fields, text[closing + len("\n---") :].lstrip("\n")


def headings(body: str) -> List[str]:
    """The `##` section titles, in order — the shape of the release."""
    return re.findall(r"(?m)^##\s+(.+?)\s*$", body)


def _mark(state: Optional[bool]) -> str:
    if state is None:
        return ":grey_question:"
    return ":white_check_mark:" if state else ":x:"


def compose(version: str) -> Tuple[str, bool]:
    """The Slack message, and whether every component is actually live."""
    entry = changelog_path(version)
    if entry.is_file():
        fields, body = split_frontmatter(entry.read_text(encoding="utf-8"))
    else:
        fields, body = {}, ""

    components = [
        ("lemma-sdk (PyPI)", pypi_has("lemma-sdk", version)),
        ("lemma-terminal (PyPI)", pypi_has("lemma-terminal", version)),
        ("lemma-sdk (npm)", npm_has("lemma-sdk", version)),
        ("GitHub Release", github_release_exists(version)),
    ]
    missing = [name for name, state in components if state is False]
    unknown = [name for name, state in components if state is None]
    complete = not missing and not unknown

    if complete:
        headline = "*Lemma {} is out* — every component is installable".format(version)
    elif missing:
        headline = "*Lemma {} is partly published* — {} still missing".format(
            version, ", ".join(missing)
        )
    else:
        headline = "*Lemma {} released* — could not verify {}".format(
            version, ", ".join(unknown)
        )

    lines = [headline]

    description = fields.get("description")
    if description:
        lines.append(description)

    section_titles = headings(body)
    if section_titles:
        lines.append("")
        lines.append("*In this release*")
        for title in section_titles[:MAX_HEADINGS]:
            lines.append("• {}".format(title))
        if len(section_titles) > MAX_HEADINGS:
            lines.append(
                "• …and {} more".format(len(section_titles) - MAX_HEADINGS)
            )
    elif not entry.is_file():
        lines.append("")
        lines.append(
            "_No changelog entry at {}_".format(entry.relative_to(REPO_ROOT))
        )

    lines.append("")
    lines.append("*Published*")
    for name, state in components:
        lines.append("{} {}".format(_mark(state), name))

    lines.append("")
    lines.append(
        "https://github.com/{}/releases/tag/v{}".format(REPOSITORY, version)
    )
    lines.append("https://lemma.work/changelog")

    return "\n".join(lines), complete


def post(text: str) -> None:
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        print(
            "::warning title=No Slack webhook configured::"
            "The release was not announced. Set the SLACK_WEBHOOK_URL "
            "repository secret."
        )
        return
    request = urllib.request.Request(
        webhook,
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
            print("Slack responded {}".format(response.status))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # Same reasoning as _read: a Slack outage is not a release failure.
        print("::warning title=Slack post failed::{}".format(error))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Release version, for example 0.8.0 or v0.8.0")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the message without posting it.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help=(
            "Exit non-zero when a component is missing from its index. For a "
            "job that should go red when a release only half-happened."
        ),
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Re-check for up to N seconds before posting, and stop as soon as "
            "every component is live. The publishes finish minutes apart, so "
            "without this a healthy release announces itself as broken -- and "
            "a false alarm every time is how a channel stops being read."
        ),
    )
    arguments = parser.parse_args()

    version = arguments.version.lstrip("vV")
    if not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+", version):
        print("Not a version: {}".format(arguments.version), file=sys.stderr)
        return 2

    deadline = time.monotonic() + max(arguments.wait_seconds, 0)
    while True:
        text, complete = compose(version)
        if complete or time.monotonic() >= deadline:
            break
        remaining = int(deadline - time.monotonic())
        print("Not every component is live yet; {}s left to wait.".format(remaining))
        time.sleep(min(POLL_SECONDS, max(remaining, 1)))

    print(text)

    if not arguments.dry_run:
        post(text)

    if arguments.require_complete and not complete:
        print(
            "Not every component is published for {}.".format(version),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
