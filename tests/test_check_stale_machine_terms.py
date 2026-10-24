"""The stale-machine-terms gate's matcher: what it flags, and what it must not.

The second half matters as much as the first. A rename gate that fires on
`app/modules/workspace` or a log event named `workspace.files.read_failed`
gets allowlisted wholesale within a week, and then guards nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_stale_machine_terms import (  # noqa: E402
    Allowance,
    find_offences,
    parse_allowlist,
)

PROMPT = "lemma-backend/app/modules/agent/prompts/workspace_cli.md"
SKILL = "lemma-skills/browser/SKILL.md"
DOC = "docs/architecture/machines.md"
CODE = "lemma-backend/app/modules/workspace/services/x.py"


def rules(path: str, text: str) -> list[str]:
    return [rule.name for _, rule, _ in find_offences(path, text)]


@pytest.mark.parametrize(
    "line",
    [
        "cd /workspace/repos/acme",
        'cwd="/workspace"',
        "files land in /workspace/c/2026-01-01/report",
        "`/workspace`",
    ],
)
def test_a_rooted_workspace_path_is_flagged_everywhere(line: str) -> None:
    assert rules(CODE, line) == ["workspace-path"]


@pytest.mark.parametrize(
    "line",
    [
        "GET /workspace/files?path=/home/user",
        "the /workspace/status route",
        "/workspace/browser/view",
        "proxied at /workspace-ports/<grant>/",
        '@covers("workspace.browser.status")',
        "operation workspace.files.list",
        'operationId: "workspace.status"',
    ],
)
def test_a_removed_route_or_operation_is_reported_once_as_a_route(line: str) -> None:
    assert rules(CODE, line) == ["old-route"]


@pytest.mark.parametrize(
    "line",
    [
        "lemma-backend/app/modules/workspace/module.py",
        "~/workspace is a user's own folder",
        "/home/user/lemma/workspaces",
        'logger.warning("workspace.files.read_failed.degraded")',
        'logger.warning("workspace.status.unavailable.degraded")',
        "the WORKSPACE_CLI toolset",
        "/machines/me/files",
    ],
)
def test_the_module_and_its_log_events_are_not_stale(line: str) -> None:
    assert rules(CODE, line) == []


def test_a_deprecated_name_may_be_documented_as_deprecated() -> None:
    assert (
        rules(DOC, "`/workspace/files` is deprecated; use `/machines/me/files`.") == []
    )
    assert (
        rules(
            DOC, "`lemma.workspace.status()` (deprecated) is `lemma.machines.startup`"
        )
        == []
    )
    # ...but only the shims' names: a deprecated *path* is still a path.
    assert rules(DOC, "the deprecated `cd /workspace/repos`") == ["workspace-path"]


def test_tests_may_exercise_the_shims() -> None:
    test = "lemma-backend/app/modules/workspace/tests/unit/test_workspace_shims.py"
    assert rules(test, 'client.get("/workspace/files")') == []
    assert rules(test, '@covers("workspace.browser.status")') == []
    assert rules(test, 'cwd="/workspace"') == ["workspace-path"]
    assert (
        rules("lemma-frontend/tests/computer.test.ts", '"/workspace/browser/view"')
        == []
    )


def test_old_generated_clients_are_flagged() -> None:
    assert rules(CODE, "new WorkspaceAppsService(client)") == ["old-sdk"]
    assert rules(CODE, "WorkspaceService.status()") == ["old-sdk"]


def test_the_deprecated_alias_is_only_flagged_in_docs_and_skills() -> None:
    assert rules(DOC, "await lemma.workspace.exec(...)") == ["old-sdk-alias"]
    assert rules(SKILL, "await lemma.workspace.exec(...)") == ["old-sdk-alias"]
    # The SDK itself defines the alias.
    assert rules("lemma-typescript/src/client.ts", "lemma.workspace.exec") == []


@pytest.mark.parametrize(
    "phrase",
    [
        "your sandbox",
        "The sandbox",
        "this sandbox",
        "the workspace sandbox",
        "your workspace",
    ],
)
def test_sandbox_in_agent_facing_text_is_flagged(phrase: str) -> None:
    assert rules(PROMPT, f"Files in {phrase} persist.") == ["agent-sandbox"]
    assert rules(SKILL, f"Files in {phrase} persist.") == ["agent-sandbox"]
    assert rules("lemma-backend/agent_tool_schemas.json", f'"{phrase}"') == [
        "agent-sandbox"
    ]


def test_sandbox_is_ordinary_prose_outside_agent_facing_text() -> None:
    assert rules(DOC, "the sandbox protocol") == []
    assert rules(CODE, "# keeps the sandbox alive") == []


def test_the_function_sandbox_keeps_its_name() -> None:
    assert rules(SKILL, "The function sandbox is deliberately minimal.") == []


def test_the_allowlist_needs_a_reason_and_a_known_rule() -> None:
    allowances = parse_allowlist(
        "# comment\n"
        "docs/release-notes/*  # history\n"
        "lemma-typescript/README.md:old-sdk-alias  # documents the alias\n"
    )
    assert allowances == [
        Allowance("docs/release-notes/*", None),
        Allowance("lemma-typescript/README.md", "old-sdk-alias"),
    ]
    assert allowances[0].covers("docs/release-notes/v0.9.0.md", "workspace-path")
    assert allowances[1].covers("lemma-typescript/README.md", "old-sdk-alias")
    assert not allowances[1].covers("lemma-typescript/README.md", "workspace-path")
    with pytest.raises(ValueError, match="no reason"):
        parse_allowlist("docs/foo.md\n")
    with pytest.raises(ValueError, match="unknown rule"):
        parse_allowlist("docs/foo.md:nope  # because\n")


def test_the_committed_allowlist_parses() -> None:
    allow = REPO_ROOT / "scripts" / "stale-machine-terms-allow.txt"
    assert parse_allowlist(allow.read_text(encoding="utf-8"))
