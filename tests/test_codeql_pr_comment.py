"""The CodeQL pull request comment: what it keeps, what it says, what it repeats.

CodeQL no longer runs locally, so this comment is how its findings reach an
author. The failure worth guarding is silence -- a finding on a changed line
that the filter drops -- and its opposite, a comment that reposts the same
inline note on every push until nobody reads them.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from codeql_pr_comment import (  # noqa: E402
    MARKER,
    already_commented,
    inline_body,
    inline_candidates,
    render_comment,
    select_findings,
    stale_alerts,
)
from codeql_scope import parse_unified_diff, unquote_path  # noqa: E402

DIFF = """\
diff --git a/app/api.py b/app/api.py
--- a/app/api.py
+++ b/app/api.py
@@ -10,0 +11,3 @@ def handler():
+    one
+    two
+    three
@@ -40 +42,0 @@ def gone():
-    removed
diff --git a/web/page.ts b/web/page.ts
--- a/web/page.ts
+++ b/web/page.ts
@@ -5 +5 @@
-old
+new
"""


def alert(
    number: int,
    path: str,
    line: int,
    *,
    rule: str = "py/sql-injection",
    level: str | None = "high",
) -> dict[str, object]:
    return {
        "number": number,
        "html_url": f"https://github.example.test/alerts/{number}",
        "rule": {"id": rule, "security_severity_level": level, "severity": "error"},
        "most_recent_instance": {
            "location": {"path": path, "start_line": line},
            "message": {"text": "Query built  from\nuser input."},
        },
    }


def test_the_diff_parser_keeps_added_lines_and_drops_pure_deletions() -> None:
    assert parse_unified_diff(DIFF.splitlines()) == {
        "app/api.py": [(11, 13)],
        "web/page.ts": [(5, 5)],
    }


def test_only_alerts_in_changed_files_survive_and_changed_lines_are_marked() -> None:
    changed = parse_unified_diff(DIFF.splitlines())
    findings = select_findings(
        [
            alert(1, "app/api.py", 12),
            alert(2, "app/api.py", 90),
            alert(3, "other.py", 12),
        ],
        changed,
        [],
    )

    assert [(f.number, f.on_changed_line) for f in findings] == [(1, True), (2, False)]
    assert findings[0].message == "Query built from user input."


def test_an_accepted_finding_is_left_out() -> None:
    changed = parse_unified_diff(DIFF.splitlines())
    findings = select_findings(
        [alert(1, "app/api.py", 12, rule="py/partial-ssrf")],
        changed,
        [("py/partial-ssrf", "app/")],
    )
    assert findings == []


def test_quality_alerts_fall_back_to_the_sarif_level() -> None:
    changed = parse_unified_diff(DIFF.splitlines())
    [finding] = select_findings([alert(4, "web/page.ts", 5, level=None)], changed, [])
    assert finding.severity == "error"
    assert not finding.inline_worthy


def test_the_comment_says_so_when_there_is_nothing_new() -> None:
    body = render_comment([], "abcdef1234", [])
    assert body.startswith(MARKER)
    assert "No new findings" in body
    assert "abcdef1" in body


def test_the_comment_lists_rule_severity_location_and_link() -> None:
    changed = parse_unified_diff(DIFF.splitlines())
    findings = select_findings(
        [alert(7, "app/api.py", 11), alert(8, "app/api.py", 90)], changed, []
    )
    body = render_comment(findings, "abcdef1234", ["python"])

    assert "1 finding(s) on lines this pull request changed" in body
    assert "| high | `py/sql-injection` | `app/api.py:11` |" in body
    assert "[#7](https://github.example.test/alerts/7)" in body
    assert "1 pre-existing finding(s)" in body
    assert "CodeQL (python) analysis did not finish" in body


def test_inline_comments_are_posted_once_per_alert() -> None:
    changed = parse_unified_diff(DIFF.splitlines())
    findings = select_findings(
        [
            alert(1, "app/api.py", 11),
            alert(2, "app/api.py", 12, level="medium"),
            alert(3, "app/api.py", 13),
        ],
        changed,
        [],
    )
    posted = already_commented(
        [inline_body(findings[0]), "an unrelated review comment"]
    )

    assert posted == {1}
    assert [f.number for f in inline_candidates(findings, posted, set())] == [3]


def test_no_inline_comment_where_main_moved_the_file_under_the_pull_request() -> None:
    changed = parse_unified_diff(DIFF.splitlines())
    findings = select_findings([alert(1, "app/api.py", 11)], changed, [])
    assert inline_candidates(findings, set(), {"app/api.py"}) == []


def test_an_added_line_starting_with_plus_plus_is_not_a_file_header() -> None:
    diff = """diff --git a/web/count.js b/web/count.js
--- a/web/count.js
+++ b/web/count.js
@@ -1,0 +2 @@
+++ counter;
@@ -5,0 +7,2 @@
+one
+two
"""
    assert parse_unified_diff(diff.splitlines()) == {"web/count.js": [(2, 2), (7, 8)]}


def test_a_quoted_utf8_path_is_kept() -> None:
    assert unquote_path('"b/caf\\303\\251.py"') == "b/café.py"
    assert unquote_path("b/plain.py") == "b/plain.py"
    diff = """diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"
--- "a/caf\\303\\251.py"
+++ "b/caf\\303\\251.py"
@@ -1 +1 @@
+x
"""
    assert parse_unified_diff(diff.splitlines()) == {"café.py": [(1, 1)]}


def test_alerts_from_another_merge_commit_are_stale() -> None:
    fresh = alert(1, "app/api.py", 12)
    fresh["most_recent_instance"]["commit_sha"] = "merge-a"  # type: ignore[index]
    newer = alert(2, "app/api.py", 13)
    newer["most_recent_instance"]["commit_sha"] = "merge-b"  # type: ignore[index]
    assert stale_alerts([fresh], "merge-a") == []
    assert stale_alerts([fresh, newer], "merge-a") == [newer]
    # An alert without a recorded commit is not evidence of staleness.
    assert stale_alerts([alert(3, "app/api.py", 14)], "merge-a") == []
