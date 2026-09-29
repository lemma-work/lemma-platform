#!/usr/bin/env python3
"""Put this pull request's CodeQL findings on the pull request itself.

CodeQL runs only in CI (`make codeql` is opt-in), so its findings have to
arrive where the author is already looking. Code scanning's own surfaces do not
do that reliably: the aggregate `CodeQL` check evaluates seconds before the
analyses upload, fails with "configurations not found", and never re-evaluates;
and the Security tab lists the whole repository's backlog, not this change.

So this reads the open alerts recorded against `refs/pull/<n>/merge` once both
analyze jobs have finished (which wait for their uploads to be processed),
keeps the ones on lines this pull request changed, and maintains one sticky
comment listing them. High and critical findings also get an inline review
comment on the line, once per alert however many times the workflow reruns.

The diff is the merge commit against its first parent, which is exactly the
tree the alerts were computed on, so line numbers agree without any mapping.
An inline comment needs a line on the pull request's head instead; it is only
posted for files whose content is identical in both, and the sticky comment
covers the rest.

Standard library and `gh` only, and no syntax newer than 3.9, so it runs on the
runner's system Python without a setup step.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from codeql_scope import (
    ChangedLines,
    is_allowed,
    load_allowlist,
    parse_unified_diff,
    touched,
)

MARKER = "<!-- lemma-codeql -->"
ALERT_MARKER = "<!-- lemma-codeql-alert:{number} -->"
INLINE_SEVERITIES = frozenset({"critical", "high"})
_SEVERITY_ORDER = ("critical", "high", "medium", "low", "error", "warning", "note")
_MAX_LISTED = 50
_JSON = Dict[str, object]


@dataclass(frozen=True)
class Finding:
    number: int
    rule: str
    severity: str
    path: str
    line: int
    message: str
    url: str
    on_changed_line: bool

    @property
    def inline_worthy(self) -> bool:
        return self.on_changed_line and self.severity in INLINE_SEVERITIES


def _field(data: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _severity(alert: _JSON) -> str:
    # Security queries carry a CVSS-style level; quality queries only the SARIF
    # one. Prefer the first, since it is what "high/critical" means.
    level = _field(alert, "rule", "security_severity_level") or _field(
        alert, "rule", "severity"
    )
    return str(level or "warning").lower()


def select_findings(
    alerts: Sequence[_JSON],
    changed: ChangedLines,
    allowed: Sequence[Tuple[str, str]],
) -> List[Finding]:
    """Alerts in files this change touched, minus the accepted ones."""
    findings: List[Finding] = []
    for alert in alerts:
        path = str(_field(alert, "most_recent_instance", "location", "path") or "")
        if path not in changed:
            continue
        rule = str(_field(alert, "rule", "id") or "?")
        if is_allowed(list(allowed), rule, path):
            continue
        line = int(_field(alert, "most_recent_instance", "location", "start_line") or 0)
        message = str(_field(alert, "most_recent_instance", "message", "text") or "")
        findings.append(
            Finding(
                number=int(alert.get("number") or 0),
                rule=rule,
                severity=_severity(alert),
                path=path,
                line=line,
                message=" ".join(message.split()),
                url=str(alert.get("html_url") or ""),
                on_changed_line=touched(changed, path, line),
            )
        )

    def rank(finding: Finding) -> Tuple[int, str, int, str]:
        order = (
            _SEVERITY_ORDER.index(finding.severity)
            if finding.severity in _SEVERITY_ORDER
            else len(_SEVERITY_ORDER)
        )
        return (order, finding.path, finding.line, finding.rule)

    return sorted(findings, key=rank)


def _row(finding: Finding) -> str:
    message = finding.message.replace("|", "\\|")
    if len(message) > 240:
        message = message[:237] + "..."
    location = f"`{finding.path}:{finding.line}`"
    link = (
        f"[#{finding.number}]({finding.url})" if finding.url else f"#{finding.number}"
    )
    return (
        f"| {finding.severity} | `{finding.rule}` | {location} | {message} | {link} |"
    )


def _table(findings: Sequence[Finding]) -> List[str]:
    rows = ["| Severity | Rule | Location | Message | Alert |", "|---|---|---|---|---|"]
    rows.extend(_row(finding) for finding in findings[:_MAX_LISTED])
    if len(findings) > _MAX_LISTED:
        rows.append(f"\n...and {len(findings) - _MAX_LISTED} more in the Security tab.")
    return rows


def render_comment(
    findings: Sequence[Finding],
    head_sha: str,
    incomplete: Sequence[str],
) -> str:
    new = [finding for finding in findings if finding.on_changed_line]
    nearby = [finding for finding in findings if not finding.on_changed_line]
    lines = [MARKER, "### CodeQL", ""]
    if new:
        lines.append(
            f"**{len(new)} finding(s) on lines this pull request changed** "
            f"(as of `{head_sha[:7]}`):"
        )
        lines.append("")
        lines.extend(_table(new))
    else:
        lines.append(
            f"No new findings on lines this pull request changed (as of `{head_sha[:7]}`)."
        )
    if nearby:
        lines.extend(
            [
                "",
                "<details>",
                f"<summary>{len(nearby)} pre-existing finding(s) elsewhere in "
                "files this pull request touched</summary>",
                "",
            ]
        )
        lines.extend(_table(nearby))
        lines.extend(["", "</details>"])
    for language in incomplete:
        lines.extend(
            [
                "",
                f"> **Warning:** the CodeQL ({language}) analysis did not finish, "
                "so this list may be missing its findings.",
            ]
        )
    lines.extend(
        [
            "",
            "<sub>Updated on every push. A finding judged safe goes in "
            "`.codeql-allow.txt` with its reason, or is dismissed in the "
            "Security tab. GitHub Code Quality posts its own review comments "
            "separately.</sub>",
        ]
    )
    return "\n".join(lines) + "\n"


def inline_body(finding: Finding) -> str:
    return (
        f"{ALERT_MARKER.format(number=finding.number)}\n"
        f"**CodeQL {finding.severity}: `{finding.rule}`**\n\n"
        f"{finding.message}\n\n"
        f"[Alert #{finding.number}]({finding.url})"
    )


def already_commented(bodies: Sequence[str]) -> Set[int]:
    posted: Set[int] = set()
    prefix = ALERT_MARKER.split("{", 1)[0]
    for body in bodies:
        for line in body.splitlines():
            if line.startswith(prefix):
                number = line[len(prefix) :].split(" ", 1)[0]
                if number.isdigit():
                    posted.add(int(number))
    return posted


def inline_candidates(
    findings: Sequence[Finding],
    posted: Set[int],
    drifted: Set[str],
) -> List[Finding]:
    """High/critical findings on changed lines not yet commented on.

    `drifted` holds the files whose merge-commit content differs from the
    pull request's head, where a merge-commit line number would land on the
    wrong line of the review diff.
    """
    return [
        finding
        for finding in findings
        if finding.inline_worthy
        and finding.number not in posted
        and finding.path not in drifted
    ]


# ── GitHub I/O ────────────────────────────────────────────────────────────────


def _gh(args: Sequence[str], payload: Optional[_JSON] = None) -> str:
    result = subprocess.run(
        ["gh", "api", *args] + (["--input", "-"] if payload is not None else []),
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def _gh_items(path: str) -> List[_JSON]:
    output = _gh(["--paginate", "--jq", ".[]", path])
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def fetch_alerts(repo: str, pr: int) -> List[_JSON]:
    try:
        return _gh_items(
            f"repos/{repo}/code-scanning/alerts?ref=refs/pull/{pr}/merge&state=open&per_page=100"
        )
    except RuntimeError as error:
        # A pull request that changed no Python or JavaScript has no analysis
        # on its merge ref at all, and the API says so with a 404.
        if "no analysis found" in str(error).lower():
            return []
        raise


def upsert_sticky(repo: str, pr: int, body: str, create: bool) -> None:
    existing = [
        comment
        for comment in _gh_items(f"repos/{repo}/issues/{pr}/comments?per_page=100")
        if MARKER in str(comment.get("body") or "")
    ]
    if existing:
        _gh(
            ["-X", "PATCH", f"repos/{repo}/issues/comments/{existing[0]['id']}"],
            {"body": body},
        )
        print(f"Updated the CodeQL comment on #{pr}.")
    elif create:
        _gh(["-X", "POST", f"repos/{repo}/issues/{pr}/comments"], {"body": body})
        print(f"Posted the CodeQL comment on #{pr}.")
    else:
        print("No analysis ran and no earlier comment exists; nothing to post.")


def post_inline(
    repo: str, pr: int, head_sha: str, findings: Sequence[Finding], drifted: Set[str]
) -> None:
    bodies = [
        str(comment.get("body") or "")
        for comment in _gh_items(f"repos/{repo}/pulls/{pr}/comments?per_page=100")
    ]
    for finding in inline_candidates(findings, already_commented(bodies), drifted):
        try:
            _gh(
                ["-X", "POST", f"repos/{repo}/pulls/{pr}/comments"],
                {
                    "body": inline_body(finding),
                    "commit_id": head_sha,
                    "path": finding.path,
                    "line": finding.line,
                    "side": "RIGHT",
                },
            )
            print(
                f"Commented inline on {finding.path}:{finding.line} ({finding.rule})."
            )
        except RuntimeError as error:
            # The sticky comment already lists it; a line GitHub will not
            # anchor to is not worth failing the job over.
            print(
                f"::warning::inline comment on {finding.path}:{finding.line} not posted: {error}"
            )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument(
        "--diff",
        required=True,
        type=Path,
        help="git diff -U0 of the merge commit against its first parent",
    )
    parser.add_argument(
        "--drifted",
        type=Path,
        help="files whose head content differs from the merge commit",
    )
    parser.add_argument("--allow", type=Path, default=Path(".codeql-allow.txt"))
    parser.add_argument(
        "--analysis",
        action="append",
        default=[],
        metavar="LANGUAGE=RESULT",
        help="each analyze job's result: success, failure, cancelled or skipped",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the comment instead of posting it"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    results = dict(entry.split("=", 1) for entry in args.analysis if "=" in entry)
    ran = any(result == "success" for result in results.values())
    incomplete = sorted(
        language
        for language, result in results.items()
        if result in {"failure", "cancelled"}
    )

    changed = parse_unified_diff(args.diff.read_text().splitlines())
    drifted: Set[str] = set()
    if args.drifted and args.drifted.is_file():
        drifted = {
            line.strip()
            for line in args.drifted.read_text().splitlines()
            if line.strip()
        }

    alerts = fetch_alerts(args.repo, args.pr) if ran else []
    findings = select_findings(alerts, changed, load_allowlist(args.allow))
    body = render_comment(findings, args.head_sha, incomplete)

    if args.dry_run:
        print(body)
        return 0
    upsert_sticky(args.repo, args.pr, body, create=ran or bool(incomplete))
    if findings:
        post_inline(args.repo, args.pr, args.head_sha, findings, drifted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
