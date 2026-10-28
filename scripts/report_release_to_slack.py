#!/usr/bin/env python3
"""Follow a release in Slack, and say what is actually installable.

Not a "we shipped" message. With ``--track`` it runs alongside the release from
the tag push and posts as things happen: that the release started, each package
as its registry starts serving it (with a link to that exact version), each
release workflow run that fails (with a link to the run), and a final summary
that names, registry by registry, what has the version and what does NOT.

This exists because releases have gone missing quietly, more than once:

- 0.7.2: the GitHub Release was published and PyPI and npm went on serving
  0.7.1 for ten days. The release was created by a workflow, and GitHub will
  not start a run from an event the default GITHUB_TOKEN raised, so nothing
  that publishes a package ever ran.
- 0.8.0: all three package workflows failed on the tag push (the version
  expression was empty on that trigger), and the failure notifier announces
  tag pushes for nothing, so the only message anyone got was the announcement
  -- which arrived because the image release succeeded.

So the source of truth is the registries, asked for the exact version, and a
component that has not landed is named in the first line of the summary. The
run fails when anything is missing, so the job goes red as well as the message.

The prose comes from the changelog entry the release already has, in
``lemma-frontend/content/changelog/`` (what lemma.work/changelog renders).

Usage::

    python3 scripts/report_release_to_slack.py v0.10.0 --track --require-complete
    python3 scripts/report_release_to_slack.py 0.10.0 --dry-run      # summary only

Reads ``SLACK_WEBHOOK_URL`` from the environment. Without it every message is
printed and nothing fails -- a missing webhook or a Slack outage must not fail a
release that otherwise succeeded. ``GITHUB_TOKEN`` and ``GITHUB_REPOSITORY``,
which Actions sets, let it read workflow runs and avoid the unauthenticated API
rate limit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_registry  # noqa: E402
from release_registry import ABSENT, PACKAGES, RELEASE_WORKFLOWS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_DIR = REPO_ROOT / "lemma-frontend/content/changelog"
REPOSITORY = os.environ.get("GITHUB_REPOSITORY") or "lemma-work/lemma-platform"

#: Slack renders a `text` payload well past this, but stops being read long
#: before it. The headings carry the shape of the release; the changelog carries
#: the rest, and it is one click away.
MAX_HEADINGS = 12

NETWORK_TIMEOUT = 20

#: How often to re-ask the registries and the Actions API while tracking. Five
#: workflows plus two GitHub reads a minute stays far inside the token's limit.
POLL_SECONDS = 60

#: Once no release run is queued or running, how long to keep watching with
#: nothing changing before giving the final word. Covers the gap between the
#: image release finishing and the Desktop release it dispatches appearing, and
#: a registry's CDN catching up with an upload.
QUIET_SECONDS = 15 * 60

#: Workflow runs that conclude in one of these are reported as failures.
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "startup_failure"}


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN", "")


def _read(url: str):
    """See ``release_registry.read_json``. Authenticated for api.github.com."""
    token = _token() if url.startswith("https://api.github.com/") else ""
    return release_registry.read_json(url, token)


def pypi_has(package: str, version: str) -> Optional[bool]:
    return release_registry.pypi_has(package, version)


def npm_has(package: str, version: str) -> Optional[bool]:
    return release_registry.npm_has(package, version)


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


def desktop_is_live(version: str) -> Optional[bool]:
    """Whether the release carries a DMG *and* installed apps are offered it.

    The GitHub Release existing says nothing about Desktop: release-local-images
    creates it, and release-desktop attaches the app afterwards, in a separate
    run that can fail on its own. 0.8.0 was announced complete with no DMG and a
    stable update feed that 404'd. So this asks the two things a person needs:
    is there an app to download, and does the feed every installed app polls --
    ``releases/latest/download/latest.json`` -- name this version.
    """
    release = _read(
        "https://api.github.com/repos/{}/releases/tags/v{}".format(REPOSITORY, version)
    )
    if release is ABSENT:
        return False
    if release is None:
        return None
    names = [asset.get("name", "") for asset in release.get("assets") or []]
    if not any(name.endswith(".dmg") for name in names):
        return False
    feed = _read(
        "https://github.com/{}/releases/latest/download/latest.json".format(REPOSITORY)
    )
    if feed is ABSENT:
        return False
    if feed is None:
        return None
    return str(feed.get("version", "")).removeprefix("v") == version


@dataclass(frozen=True)
class Component:
    """One thing a release has to put somewhere, and how to say where it is."""

    label: str
    #: "PyPI has lemma-sdk 0.10.0" -- the sentence the summary prints, with
    #: ``{verb}`` replaced by "has" or "does NOT have".
    sentence: str
    link: str
    state: Optional[bool]


def release_page(version: str) -> str:
    return "https://github.com/{}/releases/tag/v{}".format(REPOSITORY, version)


def components(version: str) -> List[Component]:
    """Every component of the release, each asked for this exact version."""
    found = []
    for package in PACKAGES:
        check = pypi_has if package.registry == "pypi" else npm_has
        found.append(
            Component(
                label=package.label,
                sentence="{} {{verb}} {} {}".format(
                    package.registry_label, package.name, version
                ),
                link=package.page(version),
                state=check(package.name, version),
            )
        )
    found.append(
        Component(
            label="GitHub Release",
            sentence="GitHub {{verb}} the v{} release".format(version),
            link=release_page(version),
            state=github_release_exists(version),
        )
    )
    found.append(
        Component(
            label="Desktop app and update feed",
            sentence="GitHub {{verb}} the Desktop {} DMG and update feed".format(
                version
            ),
            link=release_page(version),
            state=desktop_is_live(version),
        )
    )
    return found


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


def _summary_line(component: Component) -> str:
    if component.state is None:
        return ":grey_question: {}: could not check".format(
            component.sentence.format(verb="has")
        )
    if component.state:
        return ":white_check_mark: {} — {}".format(
            component.sentence.format(verb="has"), component.link
        )
    return ":x: {}".format(component.sentence.format(verb="does NOT have"))


def compose(version: str) -> Tuple[str, bool]:
    """The final summary, and whether every component is actually live."""
    entry = changelog_path(version)
    if entry.is_file():
        fields, body = split_frontmatter(entry.read_text(encoding="utf-8"))
    else:
        fields, body = {}, ""

    found = components(version)
    missing = [component.label for component in found if component.state is False]
    unknown = [component.label for component in found if component.state is None]
    complete = not missing and not unknown

    if complete:
        headline = ":white_check_mark: *Lemma {} is out* — every component is installable".format(
            version
        )
    elif missing:
        headline = (
            ":red_circle: *Lemma {} is NOT fully published* — missing: {}".format(
                version, ", ".join(missing)
            )
        )
    else:
        headline = ":warning: *Lemma {} released* — could not verify {}".format(
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
            lines.append("• …and {} more".format(len(section_titles) - MAX_HEADINGS))
    elif not entry.is_file():
        lines.append("")
        lines.append("_No changelog entry at {}_".format(entry.relative_to(REPO_ROOT)))

    lines.append("")
    lines.append("*Where {} is*".format(version))
    for component in found:
        lines.append(_summary_line(component))

    lines.append("")
    lines.append(release_page(version))
    lines.append("https://lemma.work/changelog")

    return "\n".join(lines), complete


# --- Tracking -------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """One attempt of one release workflow run, as the Actions API reports it."""

    id: int
    attempt: int
    workflow: str
    status: str
    conclusion: Optional[str]
    url: str

    @property
    def active(self) -> bool:
        return self.status != "completed"


def release_runs(
    tag: str, since: datetime, include_dispatches: bool
) -> Optional[List[Run]]:
    """Every run of a release workflow that belongs to this release.

    A run belongs to it when it was started for the tag: the tag push, a
    hand-cut release, and the Desktop build dispatched on the tag all report the
    tag as their branch. When the tracker itself was dispatched -- to follow a
    repair -- hand dispatches from any branch started since then count too,
    since that is how a failed publish is re-run. Not otherwise: the nightly
    dispatches the image release from main, and its failures are not this
    release's. None when the API could not be read, so a blip is not mistaken
    for "nothing is running".
    """
    runs = []
    since_stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = {"per_page": "100"}
    if include_dispatches:
        # No `created` filter: a repair is usually a re-run of the tag push's
        # own failed run, which keeps the creation time it had before the
        # tracker started. Its `updated_at` moves when it is re-run, so that
        # is what is compared, below.
        pass
    else:
        query["created"] = ">=" + since_stamp
        # `branch` matches the tag too, and keeps the nightly's own runs of the
        # image release from crowding this release's off the first page.
        query["branch"] = tag
    for workflow in RELEASE_WORKFLOWS:
        url = "https://api.github.com/repos/{}/actions/workflows/{}/runs?{}".format(
            REPOSITORY, workflow, urllib.parse.urlencode(query)
        )
        data = _read(url)
        if data is ABSENT:
            continue
        if data is None:
            return None
        for item in data.get("workflow_runs") or []:
            ours = item.get("head_branch") == tag or (
                include_dispatches and item.get("event") == "workflow_dispatch"
            )
            if include_dispatches and (item.get("updated_at") or "") < since_stamp:
                # Untouched since the tracker started: not part of this repair.
                ours = False
            if ours:
                runs.append(
                    Run(
                        id=int(item["id"]),
                        attempt=int(item.get("run_attempt") or 1),
                        workflow=item.get("name") or workflow,
                        status=item.get("status") or "",
                        conclusion=item.get("conclusion"),
                        url=item.get("html_url") or "",
                    )
                )
    return runs


def started_message(version: str, sha: str, tracker_url: str) -> str:
    targets = ", ".join(
        "{} → {}".format(package.name, package.registry_label) for package in PACKAGES
    )
    lines = [
        ":rocket: *Lemma {} release started* — tag `v{}`{}".format(
            version, version, " (`{}`)".format(sha[:7]) if sha else ""
        ),
        "Publishing {}, the GitHub Release and Desktop. Progress follows here; "
        "the last message says what landed and what did not.".format(targets),
    ]
    if tracker_url:
        lines.append(tracker_url)
    return "\n".join(lines)


def landed_message(version: str, component: Component) -> str:
    return ":white_check_mark: {} — {}".format(
        component.sentence.format(verb="has"), component.link
    )


def failed_message(version: str, run: Run) -> str:
    attempt = " (attempt {})".format(run.attempt) if run.attempt > 1 else ""
    return ":x: *{} failed* for v{}{} — {}".format(
        run.workflow, version, attempt, run.url
    )


@dataclass
class Tracker:
    """What has been said so far, so each event is posted exactly once."""

    version: str
    landed: set[str] = field(default_factory=set)
    reported_runs: set[tuple[int, int]] = field(default_factory=set)
    seen_runs: dict[tuple[int, int], str] = field(default_factory=dict)
    last_change: float = 0.0

    def observe(
        self, found: List[Component], runs: Optional[List[Run]], now: float
    ) -> List[str]:
        """The messages this observation adds, oldest news first."""
        messages = []
        for component in found:
            if component.state and component.label not in self.landed:
                self.landed.add(component.label)
                messages.append(landed_message(self.version, component))
                self.last_change = now
        for run in runs or []:
            key = (run.id, run.attempt)
            if self.seen_runs.get(key) != run.status:
                self.seen_runs[key] = run.status
                self.last_change = now
            if (
                not run.active
                and run.conclusion in FAILED_CONCLUSIONS
                and key not in self.reported_runs
            ):
                self.reported_runs.add(key)
                messages.append(failed_message(self.version, run))
        return messages

    def settled(
        self, runs: Optional[List[Run]], now: float, quiet_seconds: float
    ) -> bool:
        """Nothing is running and nothing has changed for ``quiet_seconds``."""
        if runs is None or any(run.active for run in runs):
            return False
        return now - self.last_change >= quiet_seconds


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
        data=json.dumps({"text": text, "unfurl_links": False}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
            print("Slack responded {}".format(response.status))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        # Same reasoning as _read: a Slack outage is not a release failure.
        print("::warning title=Slack post failed::{}".format(error))


def track(
    version: str,
    *,
    say: Callable[[str], None],
    wait_seconds: int,
    quiet_seconds: int = QUIET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Post progress until the release has landed, gone quiet, or run out of time."""
    since = datetime.now(timezone.utc) - timedelta(minutes=10)
    include_dispatches = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    tracker_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        tracker_url = "{}/{}/actions/runs/{}".format(
            os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
            REPOSITORY,
            os.environ["GITHUB_RUN_ID"],
        )
    say(started_message(version, os.environ.get("GITHUB_SHA", ""), tracker_url))

    tracker = Tracker(version, last_change=clock())
    deadline = clock() + max(wait_seconds, 0)
    while True:
        found = components(version)
        runs = release_runs("v" + version, since, include_dispatches)
        for message in tracker.observe(found, runs, clock()):
            say(message)
        if all(component.state for component in found):
            return
        if clock() >= deadline or tracker.settled(runs, clock(), quiet_seconds):
            return
        sleep(POLL_SECONDS)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("version", help="Release version, for example 0.8.0 or v0.8.0")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the messages without posting them.",
    )
    parser.add_argument(
        "--track",
        action="store_true",
        help=(
            "Post that the release started, each package as it lands and each "
            "release run that fails, then the summary."
        ),
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help=(
            "Exit non-zero when a component is missing or could not be checked, "
            "so the job goes red when a release only half-happened."
        ),
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Keep watching for up to N seconds before the summary, and stop as "
            "soon as every component is live. The publishes finish minutes "
            "apart, so without this a healthy release announces itself as broken."
        ),
    )
    arguments = parser.parse_args(argv)

    try:
        version = release_registry.normalize_version(arguments.version)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    def say(text: str) -> None:
        print(text)
        print()
        if not arguments.dry_run:
            post(text)

    if arguments.track:
        track(version, say=say, wait_seconds=arguments.wait_seconds)
    else:
        deadline = time.monotonic() + max(arguments.wait_seconds, 0)
        while not all(component.state for component in components(version)):
            if time.monotonic() >= deadline:
                break
            time.sleep(POLL_SECONDS)

    text, complete = compose(version)
    say(text)

    if arguments.require_complete and not complete:
        print(
            "::error title=Lemma {} is not fully published::{}".format(
                version, text.splitlines()[0]
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
