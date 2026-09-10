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
    """Import the script by path; `scripts/` is not a package."""
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


def _live(monkeypatch, *, pypi, npm, release):
    monkeypatch.setattr(report, "pypi_has", lambda package, version: pypi)
    monkeypatch.setattr(report, "npm_has", lambda package, version: npm)
    monkeypatch.setattr(report, "github_release_exists", lambda version: release)


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


def test_the_0_7_2_shape_is_reported_as_partly_published(monkeypatch, entry):
    """A GitHub Release with nothing on the indexes: the case that went unseen."""
    _live(monkeypatch, pypi=False, npm=False, release=True)
    text, complete = report.compose("9.9.9")
    assert complete is False
    assert "partly published" in text
    # The missing components are named in the first line, not buried below.
    headline = text.splitlines()[0]
    assert "lemma-sdk (PyPI)" in headline
    assert "lemma-sdk (npm)" in headline


def test_an_unreachable_index_is_never_reported_as_published(monkeypatch, entry):
    _live(monkeypatch, pypi=None, npm=None, release=None)
    text, complete = report.compose("9.9.9")
    assert complete is False
    assert "could not verify" in text
    assert ":white_check_mark:" not in text


def test_a_404_is_an_answer_rather_than_a_failure_to_get_one(monkeypatch, entry):
    """A missing package must read as missing, not as "could not check"."""
    monkeypatch.setattr(report, "_read", lambda url: report.ABSENT)
    assert report.pypi_has("lemma-sdk", "9.9.9") is False
    assert report.npm_has("lemma-sdk", "9.9.9") is False
    assert report.github_release_exists("9.9.9") is False


def test_a_transport_error_stays_unknown(monkeypatch, entry):
    monkeypatch.setattr(report, "_read", lambda url: None)
    assert report.pypi_has("lemma-sdk", "9.9.9") is None
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
