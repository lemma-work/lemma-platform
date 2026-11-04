"""The release announcement must be able to say a release did not happen.

The script exists because 0.7.2 looked like a release from every angle this
repository could see, and shipped no packages. So the test that matters is not
"does it format a nice message" -- it is that a half-published release is
reported as half-published, and that an index it could not reach is never
silently reported as a success.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    """Import the script by path; `scripts/` is not a package.

    The script puts its own directory on `sys.path` to import release_registry,
    the same way it does when run as `python3 scripts/...`.
    """
    path = REPO_ROOT / "scripts" / "report_release_to_slack.py"
    spec = importlib.util.spec_from_file_location("report_release_to_slack", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = _load()


ENTRY = """---
title: 9.9.9
description: A one-line summary.
published: 2026-01-01
tags:
  - release
---

Opening paragraph.

## First thing

Body.

## Second thing

More body.
"""


def _live(monkeypatch, *, pypi, npm, release, desktop=True):
    monkeypatch.setattr(report, "pypi_has", lambda package, version: pypi)
    monkeypatch.setattr(report, "npm_has", lambda package, version: npm)
    monkeypatch.setattr(report, "github_release_exists", lambda version: release)
    monkeypatch.setattr(report, "desktop_is_live", lambda version: desktop)


registry = report.release_registry


@pytest.fixture
def entry(monkeypatch, tmp_path):
    (tmp_path / "9-9-9.mdx").write_text(ENTRY, encoding="utf-8")
    monkeypatch.setattr(report, "CHANGELOG_DIR", tmp_path)
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)


def test_frontmatter_and_headings_come_from_the_changelog(entry):
    fields, body = report.split_frontmatter(ENTRY)
    assert fields["description"] == "A one-line summary."
    assert "tags" not in fields or fields["tags"] != "- release"
    assert report.headings(body) == ["First thing", "Second thing"]


def test_everything_live_reads_as_a_release(monkeypatch, entry):
    _live(monkeypatch, pypi=True, npm=True, release=True)
    text, complete = report.compose("9.9.9")
    assert complete is True
    assert "every component is installable" in text
    assert "A one-line summary." in text
    assert "• First thing" in text


def test_the_0_7_2_shape_is_reported_as_not_fully_published(monkeypatch, entry):
    """A GitHub Release with nothing on the indexes: the case that went unseen."""
    _live(monkeypatch, pypi=False, npm=False, release=True)
    text, complete = report.compose("9.9.9")
    assert complete is False
    # The missing components are named in the first line, not buried below.
    headline = text.splitlines()[0]
    assert headline.startswith(":red_circle:")
    assert "NOT fully published" in headline
    assert "lemma-sdk (PyPI)" in headline
    assert "lemma-terminal (PyPI)" in headline
    assert "lemma-sdk (npm)" in headline


def test_the_summary_says_registry_by_registry_what_is_missing(monkeypatch, entry):
    """Each registry gets its own line, and absence is said in so many words."""
    monkeypatch.setattr(
        report, "pypi_has", lambda package, version: package == "lemma-sdk"
    )
    monkeypatch.setattr(report, "npm_has", lambda package, version: False)
    monkeypatch.setattr(report, "github_release_exists", lambda version: True)
    monkeypatch.setattr(report, "desktop_is_live", lambda version: True)
    text, complete = report.compose("9.9.9")
    assert complete is False
    assert (
        ":white_check_mark: PyPI has lemma-sdk 9.9.9 — "
        "https://pypi.org/project/lemma-sdk/9.9.9/"
    ) in text
    assert ":x: PyPI does NOT have lemma-terminal 9.9.9" in text
    assert ":x: npm does NOT have lemma-sdk 9.9.9" in text
    assert text.splitlines()[0].endswith(
        "missing: lemma-terminal (PyPI), lemma-sdk (npm)"
    )


def test_the_summary_covers_every_published_package(monkeypatch, entry):
    """A package added to release_registry.PACKAGES is in the summary for free."""
    _live(monkeypatch, pypi=True, npm=True, release=True)
    text, _ = report.compose("9.9.9")
    for package in registry.PACKAGES:
        assert "{} has {} 9.9.9".format(package.registry_label, package.name) in text


def test_an_unreachable_index_is_never_reported_as_published(monkeypatch, entry):
    _live(monkeypatch, pypi=None, npm=None, release=None, desktop=None)
    text, complete = report.compose("9.9.9")
    assert complete is False
    assert "could not verify" in text
    assert ":white_check_mark:" not in text


def _registry_answers(monkeypatch, answer):
    monkeypatch.setattr(registry, "read_json", lambda url, token="": answer)


def test_a_404_is_an_answer_rather_than_a_failure_to_get_one(monkeypatch, entry):
    """A missing package must read as missing, not as "could not check"."""
    _registry_answers(monkeypatch, registry.ABSENT)
    assert report.pypi_has("lemma-sdk", "9.9.9") is False
    assert report.npm_has("lemma-sdk", "9.9.9") is False
    assert report.github_release_exists("9.9.9") is False


def test_a_pypi_version_with_no_files_is_not_installable(monkeypatch, entry):
    """Known to PyPI but carrying nothing: `pip install` fails on it.

    Every file yanked or deleted, or an upload that never completed. PyPI knows
    the version's name and cannot serve it.
    """
    _registry_answers(monkeypatch, {"info": {"version": "9.9.9"}, "urls": []})
    assert report.pypi_has("lemma-sdk", "9.9.9") is False

    _registry_answers(
        monkeypatch,
        {"info": {"version": "9.9.9"}, "urls": [{"filename": "lemma_sdk-9.9.9.whl"}]},
    )
    assert report.pypi_has("lemma-sdk", "9.9.9") is True


def test_the_registries_are_asked_for_the_exact_version(monkeypatch):
    asked = []
    monkeypatch.setattr(
        registry,
        "read_json",
        lambda url, token="": asked.append(url) or registry.ABSENT,
    )
    registry.pypi_has("lemma-terminal", "9.9.9")
    registry.npm_has("lemma-sdk", "9.9.9")
    assert asked == [
        "https://pypi.org/pypi/lemma-terminal/9.9.9/json",
        "https://registry.npmjs.org/lemma-sdk/9.9.9",
    ]


def test_a_transport_error_stays_unknown(monkeypatch, entry):
    _registry_answers(monkeypatch, None)
    assert report.pypi_has("lemma-sdk", "9.9.9") is None
    assert report.npm_has("lemma-sdk", "9.9.9") is None
    assert report.github_release_exists("9.9.9") is None


def test_a_missing_changelog_entry_is_said_out_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(report, "CHANGELOG_DIR", tmp_path)
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)
    _live(monkeypatch, pypi=True, npm=True, release=True)
    text, complete = report.compose("9.9.9")
    assert complete is True
    assert "No changelog entry" in text


def test_the_changelog_filename_uses_dashes():
    assert report.changelog_path("0.8.0").name == "0-8-0.mdx"


def test_a_missing_webhook_does_not_fail_the_release(monkeypatch, capsys):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    report.post("anything")
    assert "No Slack webhook configured" in capsys.readouterr().out


def test_a_release_without_desktop_is_not_complete(monkeypatch, entry):
    """0.8.0 was announced complete with no DMG and a 404 update feed."""
    _live(monkeypatch, pypi=True, npm=True, release=True, desktop=False)
    text, complete = report.compose("9.9.9")
    assert complete is False
    assert "Desktop app and update feed" in text


# --- The version every release step agrees on --------------------------------


@pytest.mark.parametrize("raw", ["v0.10.0", "0.10.0", "V0.10.0", " v0.10.0\n"])
def test_a_tag_or_a_version_resolves_to_the_version(raw):
    assert registry.normalize_version(raw) == "0.10.0"


@pytest.mark.parametrize("raw", ["", "v", "main", "refs/tags/v0.10.0", "0.10"])
def test_anything_else_is_refused_rather_than_published_under(raw):
    """Empty above all: every v0.8.0 tag push resolved its version to ""."""
    with pytest.raises(ValueError):
        registry.normalize_version(raw)


def test_the_version_command_writes_the_step_output(monkeypatch, tmp_path):
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert registry.main(["version", "v1.2.3"]) == 0
    assert output.read_text() == "version=1.2.3\n"
    assert registry.main(["version", ""]) == 1


@pytest.mark.parametrize("state, expected", [(True, "true"), (False, "false")])
def test_published_answers_for_the_step_that_decides_to_upload(
    monkeypatch, tmp_path, state, expected
):
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setitem(registry.CHECKS, "pypi", lambda package, version: state)
    assert registry.main(["published", "pypi", "lemma-sdk", "9.9.9"]) == 0
    assert output.read_text() == "published={}\n".format(expected)


def test_published_fails_rather_than_guess_when_the_registry_is_unreachable(
    monkeypatch, tmp_path
):
    """Guessing "yes" would skip a publish that never happened, and pass."""
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setitem(registry.CHECKS, "npm", lambda package, version: None)
    monkeypatch.setattr(registry.time, "sleep", lambda seconds: None)
    assert registry.main(["published", "npm", "lemma-sdk", "9.9.9"]) == 1
    assert not output.exists()


def test_wait_fails_loudly_when_the_version_never_lands(monkeypatch, capsys):
    monkeypatch.setitem(registry.CHECKS, "pypi", lambda package, version: False)
    monkeypatch.setattr(registry.time, "sleep", lambda seconds: None)
    code = registry.main(
        [
            "wait",
            "pypi",
            "lemma-sdk",
            "9.9.9",
            "--timeout-seconds",
            "0",
            "--hint",
            "Do X.",
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "::error title=lemma-sdk 9.9.9 is not on PyPI::" in out
    assert out.rstrip().endswith("Do X.")


def test_wait_returns_as_soon_as_the_version_lands(monkeypatch):
    answers = iter([None, False, True])
    monkeypatch.setitem(registry.CHECKS, "npm", lambda package, version: next(answers))
    monkeypatch.setattr(registry.time, "sleep", lambda seconds: None)
    assert registry.main(["wait", "npm", "lemma-sdk", "9.9.9"]) == 0


# --- Following a release as it happens ---------------------------------------


def _component(label, state):
    return report.Component(
        label=label,
        sentence="PyPI {verb} " + label + " 9.9.9",
        link="https://example.invalid/" + label,
        state=state,
    )


def _run(run_id, status="completed", conclusion="success", attempt=1):
    return report.Run(
        id=run_id,
        attempt=attempt,
        workflow="Release Lemma Python SDK",
        status=status,
        conclusion=conclusion,
        url="https://github.com/o/r/actions/runs/{}".format(run_id),
    )


def test_each_package_is_announced_once_with_its_link():
    tracker = report.Tracker("9.9.9")
    first = tracker.observe(
        [_component("lemma-sdk", True), _component("x", None)], [], 1.0
    )
    assert first == [
        ":white_check_mark: PyPI has lemma-sdk 9.9.9 — https://example.invalid/lemma-sdk"
    ]
    assert tracker.observe([_component("lemma-sdk", True)], [], 2.0) == []


def test_each_failed_run_is_announced_once_with_its_link():
    tracker = report.Tracker("9.9.9")
    running = [_run(7, status="in_progress", conclusion=None)]
    assert tracker.observe([], running, 1.0) == []
    failed = [_run(7, conclusion="failure")]
    assert tracker.observe([], failed, 2.0) == [
        ":x: *Release Lemma Python SDK failed* for v9.9.9 — "
        "https://github.com/o/r/actions/runs/7"
    ]
    assert tracker.observe([], failed, 3.0) == []
    # A re-run is a new attempt, and if it fails too that is news again.
    again = [_run(7, conclusion="failure", attempt=2)]
    assert tracker.observe([], again, 4.0) == [
        ":x: *Release Lemma Python SDK failed* for v9.9.9 (attempt 2) — "
        "https://github.com/o/r/actions/runs/7"
    ]


def test_a_successful_run_is_not_news_but_its_package_landing_is():
    tracker = report.Tracker("9.9.9")
    assert tracker.observe([_component("lemma-sdk", False)], [_run(1)], 1.0) == []


def test_tracking_waits_while_anything_runs_and_then_for_quiet():
    tracker = report.Tracker("9.9.9", last_change=0.0)
    running = [_run(1, status="queued", conclusion=None)]
    tracker.observe([], running, 0.0)
    assert not tracker.settled(running, 10_000.0, 900)
    done = [_run(1)]
    tracker.observe([], done, 100.0)
    assert not tracker.settled(done, 500.0, 900)
    assert tracker.settled(done, 1_000.0, 900)
    # The Actions API not answering is not "nothing is running".
    assert not tracker.settled(None, 10_000.0, 900)


def test_tracking_posts_start_progress_failure_and_stops_when_settled(
    monkeypatch, entry
):
    """The whole conversation for a release whose npm publish failed."""
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_SHA", "abcdef1234567")
    monkeypatch.setattr(report, "pypi_has", lambda package, version: True)
    monkeypatch.setattr(report, "npm_has", lambda package, version: False)
    monkeypatch.setattr(report, "github_release_exists", lambda version: True)
    monkeypatch.setattr(report, "desktop_is_live", lambda version: True)
    failed = report.Run(
        id=9,
        attempt=1,
        workflow="Release Lemma TypeScript SDK",
        status="completed",
        conclusion="failure",
        url="https://github.com/o/r/actions/runs/9",
    )
    monkeypatch.setattr(report, "release_runs", lambda tag, since, dispatches: [failed])
    now = [0.0]
    said = []
    report.track(
        "9.9.9",
        say=said.append,
        wait_seconds=10_000,
        quiet_seconds=900,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert said[0].startswith(
        ":rocket: *Lemma 9.9.9 release started* — tag `v9.9.9` (`abcdef1`)"
    )
    assert said[0].endswith("/actions/runs/42")
    assert ":white_check_mark: PyPI has lemma-terminal 9.9.9" in "\n".join(said)
    assert (
        said.count(
            ":x: *Release Lemma TypeScript SDK failed* for v9.9.9 — https://github.com/o/r/actions/runs/9"
        )
        == 1
    )
    assert not any("npm has" in message for message in said)
    # Stopped once quiet, well before the deadline.
    assert now[0] < 10_000


def test_tracking_stops_as_soon_as_everything_has_landed(monkeypatch, entry):
    _live(monkeypatch, pypi=True, npm=True, release=True)
    monkeypatch.setattr(report, "release_runs", lambda tag, since, dispatches: [])
    said = []
    report.track("9.9.9", say=said.append, wait_seconds=10_000, sleep=pytest.fail)
    assert len(said) == 1 + len(registry.PACKAGES) + 2


def test_an_incomplete_release_fails_the_job(monkeypatch, entry, capsys):
    _live(monkeypatch, pypi=True, npm=False, release=True)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert report.main(["v9.9.9", "--require-complete"]) == 1
    assert (
        "::error title=Lemma 9.9.9 is not fully published::" in capsys.readouterr().out
    )
    _live(monkeypatch, pypi=True, npm=True, release=True)
    assert report.main(["v9.9.9", "--require-complete"]) == 0


def test_following_a_repair_counts_a_rerun_of_the_tag_push(monkeypatch):
    """A repair is usually a re-run of the tag push's failed run, which keeps
    the creation time it had before the tracker started. So a dispatched tracker
    must not ask by creation time, and must keep only what moved since it began."""
    from datetime import datetime, timezone

    asked = []

    def runs_page(url, *_args, **_kwargs):
        asked.append(url)
        return {
            "workflow_runs": [
                {
                    "id": 1,
                    "run_attempt": 2,
                    "head_branch": "v0.10.0",
                    "event": "push",
                    "status": "in_progress",
                    "conclusion": None,
                    "html_url": "u1",
                    "created_at": "2026-10-25T08:00:00Z",
                    "updated_at": "2026-10-25T12:05:00Z",
                },
                {
                    "id": 2,
                    "run_attempt": 1,
                    "head_branch": "v0.10.0",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "u2",
                    "created_at": "2026-10-25T08:00:00Z",
                    "updated_at": "2026-10-25T08:30:00Z",
                },
            ]
        }

    monkeypatch.setattr(report, "_read", runs_page)
    since = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    runs = report.release_runs("v0.10.0", since, True)
    assert all("created" not in url for url in asked)
    assert {run.id for run in runs} == {1}
