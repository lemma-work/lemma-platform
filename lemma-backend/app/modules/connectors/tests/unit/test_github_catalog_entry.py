"""Properties of the committed GitHub catalog entry.

The entry is generated offline by `scripts/generate_github_static_operations.py`
from GitHub's own OpenAPI description. That script is not on the runtime import
path, so nothing else would notice a regeneration that quietly lost the pruning,
dropped the token-kind flag, or reintroduced the schema bloat.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CONFIG = Path(__file__).resolve().parents[5] / "scripts" / "lemma_apps_config.json"


@pytest.fixture(scope="module")
def github_operations() -> list[dict]:
    apps = json.loads(_CONFIG.read_text(encoding="utf-8"))
    entry = next(app for app in apps if app["name"] == "github")
    return entry["static_operations"]


def test_the_curated_set_covers_actions_end_to_end(github_operations):
    """Actions was absent entirely, which is the gap this connector existed
    around: a dev-team app could not see a run, let alone start one."""
    names = {op["name"] for op in github_operations}
    for required in (
        "actions_list_workflow_runs",
        "actions_get_workflow_run",
        "actions_list_jobs_for_workflow_run",
        "actions_download_workflow_run_logs",
        "actions_list_workflow_run_artifacts",
        "actions_create_workflow_dispatch",
        "actions_cancel_workflow_run",
        "actions_re_run_workflow",
    ):
        assert required in names, required


def test_operations_that_must_exist_for_ordinary_work(github_operations):
    names = {op["name"] for op in github_operations}
    # `create-ref` was missing outright, so nothing could open a branch.
    assert "git_create_ref" in names
    assert "checks_create" in names
    assert "pulls_submit_review" in names
    assert "issues_create_label" in names
    # Projects v2 has no REST surface at all.
    assert "github_graphql_request" in names
    assert "github_http_request" in names


def test_nothing_destructive_or_secret_bearing_is_exposed(github_operations):
    """`gh` in the sandbox is there for these. An operation in the catalog is
    callable by any agent holding `connector.use`, and there is no per-install
    disable list to walk it back with."""
    names = {op["name"] for op in github_operations}
    for forbidden in (
        "repos_delete",
        "repos_transfer",
        "actions_create_or_update_repo_secret",
        "actions_delete_repo_secret",
        "actions_create_repo_variable",
        "repos_update_branch_protection",
        "repos_delete_branch_protection",
    ):
        assert forbidden not in names, forbidden


def test_every_operation_declares_which_token_can_run_it(github_operations):
    for op in github_operations:
        kind = op["execution"].get("github_token_kind")
        assert kind in {"installation_ok", "user_only"}, op["name"]


def test_the_user_only_operations_are_the_ones_github_says_they_are(
    github_operations,
):
    """Derived from `x-github.enabledForGitHubApps`, not hand-listed. Two of
    these are load-bearing today -- pod publish calls both."""
    user_only = {
        op["name"]
        for op in github_operations
        if op["execution"]["github_token_kind"] == "user_only"
    }
    assert "users_get_authenticated" in user_only
    assert "repos_create_for_authenticated_user" in user_only
    assert "gists_create" in user_only
    # And an ordinary repo-scoped call is not.
    assert "issues_create" not in user_only


def test_output_schemas_are_pruned_to_one_level(github_operations):
    """Output schemas were 93% of this entry and `repos_get` alone was 72 KB --
    roughly 18k tokens for a single describe call."""

    def deepest_properties(node: object, depth: int = 0) -> int:
        if not isinstance(node, dict):
            return depth
        worst = depth
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for sub in value.values():
                    worst = max(worst, deepest_properties(sub, depth + 1))
            elif isinstance(value, dict):
                worst = max(worst, deepest_properties(value, depth))
        return worst

    for op in github_operations:
        schema = op.get("output_schema")
        if schema is None:
            continue
        assert deepest_properties(schema) <= 1, op["name"]


def test_the_entry_stays_small_enough_to_describe(github_operations):
    """A budget, not a measurement. Tripling the operation count while halving
    the bytes is the whole point; a regeneration that loses the pruning would
    otherwise pass every other test here."""
    total = len(json.dumps(github_operations))
    assert total < 700_000, f"{total:,} bytes"
    biggest = max(len(json.dumps(op)) for op in github_operations)
    assert biggest < 25_000, f"{biggest:,} bytes"


def test_input_schemas_keep_full_fidelity(github_operations):
    """Only *output* schemas are pruned. Inputs are what
    `run_connector_operation` validates arguments against."""
    create_issue = next(op for op in github_operations if op["name"] == "issues_create")
    properties = create_issue["input_schema"]["properties"]
    assert properties["owner"].get("description")
    # The request body is a nested object, and it keeps its nesting and its
    # prose -- which is exactly what the output side gives up.
    title = properties["body"]["properties"]["title"]
    assert title.get("description")
    assert "oneOf" in title
