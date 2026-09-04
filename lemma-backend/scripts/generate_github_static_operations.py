"""One-time offline generator for the GitHub connector's ``static_operations``.

Not part of the runtime import path (``import_connector_catalog.py`` never
imports this module). Run by hand whenever the curated operation set changes,
and paste the output into ``lemma_apps_config.json``'s ``"github"`` entry
under ``static_operations``.

The spec this reads is GitHub's own official REST API OpenAPI description,
fetched on demand from ``SPEC_URL`` below and cached at ``SPEC_PATH`` --
deliberately *not* committed to the repo (see ``.gitignore``): it's a vendor
doc full of realistic-looking example secret values (fake access_token/
client_secret/webhook_secret strings used purely to illustrate schema shape),
which a secret scanner cannot distinguish from a real leaked credential, so
keeping it out of git history avoids that fight entirely. Delete the cached
copy and re-run to pick up a newer GitHub API version.

``build_operation_descriptors`` walks the *whole* spec but only materializes
the operations named in ``ALLOWLIST`` below, so nothing needs pre-trimming
out of the source file.

Usage::

    python scripts/generate_github_static_operations.py
    python scripts/generate_github_static_operations.py --write   # splice directly into lemma_apps_config.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from app.modules.connectors.infrastructure.openapi.spec_import import (  # noqa: E402
    OpenAPIOperation,
    build_operation_descriptors,
    build_raw_passthrough,
)

SPEC_URL = (
    "https://raw.githubusercontent.com/github/rest-api-description/main/"
    "descriptions/api.github.com/api.github.com.json"
)
SPEC_PATH = (
    Path(__file__).parent.parent / "lemma-connectors" / "openapi_specs" / "github.json"
)
LEMMA_APPS_CONFIG_PATH = Path(__file__).parent / "lemma_apps_config.json"


def _ensure_spec() -> None:
    if SPEC_PATH.exists():
        return
    print(f"Fetching {SPEC_URL} -> {SPEC_PATH} (not committed; see .gitignore) ...")
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(SPEC_URL, timeout=60) as response:  # noqa: S310
        SPEC_PATH.write_bytes(response.read())


SERVER_URL = "https://api.github.com"

DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "lemma-connectors",
}

# Curated subset of GitHub's ~1000 REST operations: the ones an agent doing
# real dev work on a repo actually reaches for. Not exhaustive by design --
# broad enough to cover repo browsing, file read/write, issues, PRs, releases,
# gists and search, narrow enough to stay a legible tool catalog. Extend this
# list (operationIds only, verified against the spec) rather than importing
# everything.
ALLOWLIST = [
    {"operation_id": op_id}
    for op_id in [
        # Identity
        "users/get-authenticated",
        "users/get-by-username",
        # Repositories
        "repos/get",
        "repos/list-for-authenticated-user",
        "repos/list-for-org",
        "repos/create-for-authenticated-user",
        "repos/list-branches",
        "repos/get-branch",
        "repos/list-commits",
        "repos/get-commit",
        "repos/list-collaborators",
        "repos/list-tags",
        # File contents
        "repos/get-content",
        "repos/create-or-update-file-contents",
        "repos/delete-file",
        # Issues
        "issues/list-for-repo",
        "issues/get",
        "issues/create",
        "issues/update",
        "issues/list-comments",
        "issues/create-comment",
        "issues/list-labels-on-issue",
        "issues/add-labels",
        # Pull requests
        "pulls/list",
        "pulls/get",
        "pulls/create",
        "pulls/update",
        "pulls/merge",
        "pulls/list-files",
        "pulls/list-reviews",
        "pulls/create-review",
        # Gists
        "gists/create",
        "gists/list",
        "gists/get",
        # Releases
        "repos/list-releases",
        "repos/get-latest-release",
        "repos/create-release",
        "repos/upload-release-asset",
        "repos/download-tarball-archive",
        "repos/download-zipball-archive",
        # Git data. Enough to build a commit out of band -- what publishing a
        # pod as a repository needs, and what an agent needs to write several
        # files as one commit rather than one PUT per file.
        "git/get-ref",
        "git/update-ref",
        "git/create-blob",
        "git/create-tree",
        "git/create-commit",
        # Search
        "search/repos",
        "search/issues-and-pull-requests",
        "search/code",
        # Orgs
        "orgs/list-for-authenticated-user",
        # GitHub Actions. Run history, the logs and artifacts a run leaves behind, and
        # the controls a dev-team app needs over work in flight. Secrets and
        # variables are list-only on purpose: rotating one is a `gh` job, not an
        # agent's.
        "actions/list-repo-workflows",
        "actions/get-workflow",
        "actions/enable-workflow",
        "actions/disable-workflow",
        "actions/create-workflow-dispatch",
        "actions/list-workflow-runs",
        "actions/list-workflow-runs-for-repo",
        "actions/get-workflow-run",
        "actions/cancel-workflow-run",
        "actions/re-run-workflow",
        "actions/re-run-workflow-failed-jobs",
        "actions/list-jobs-for-workflow-run",
        "actions/get-job-for-workflow-run",
        "actions/download-job-logs-for-workflow-run",
        "actions/download-workflow-run-logs",
        "actions/list-workflow-run-artifacts",
        "actions/get-artifact",
        "actions/download-artifact",
        "actions/delete-artifact",
        "actions/get-workflow-run-usage",
        "actions/list-repo-secrets",
        "actions/list-repo-variables",
        "actions/list-environment-secrets",
        "actions/get-pending-deployments-for-run",
        "actions/review-pending-deployments-for-run",
        "repos/create-dispatch-event",
        "repos/get-all-environments",
        # Checks and commit statuses -- how anything reports back on a ref.
        "checks/create",
        "checks/update",
        "checks/get",
        "checks/list-for-ref",
        "checks/list-suites-for-ref",
        "repos/create-commit-status",
        "repos/get-combined-status-for-ref",
        "repos/list-commit-statuses-for-ref",
        # Review, rather than just open and merge.
        "pulls/list-review-comments",
        "pulls/create-review-comment",
        "pulls/create-reply-for-review-comment",
        "pulls/submit-review",
        "pulls/dismiss-review",
        "pulls/update-review",
        "pulls/request-reviewers",
        "pulls/remove-requested-reviewers",
        "pulls/list-requested-reviewers",
        "pulls/list-commits",
        "pulls/update-branch",
        "pulls/check-if-merged",
        # Triage: labels, assignees, milestones, and the timeline that explains how
        # an issue got where it is.
        "issues/list-labels-for-repo",
        "issues/create-label",
        "issues/update-label",
        "issues/delete-label",
        "issues/remove-label",
        "issues/set-labels",
        "issues/add-assignees",
        "issues/remove-assignees",
        "issues/lock",
        "issues/unlock",
        "issues/update-comment",
        "issues/delete-comment",
        "issues/get-comment",
        "issues/list-milestones",
        "issues/create-milestone",
        "issues/update-milestone",
        "issues/list-events-for-timeline",
        "issues/list-for-authenticated-user",
        "issues/list-assignees",
        # Repository lifecycle. No delete and no transfer: an agent should not be
        # able to end a repository, and `gh` is there when a person means to.
        "repos/update",
        "repos/create-in-org",
        "repos/create-using-template",
        "repos/create-fork",
        "repos/list-languages",
        "repos/get-all-topics",
        "repos/replace-all-topics",
        "repos/get-readme",
        "repos/list-contributors",
        "repos/list-teams",
        "repos/add-collaborator",
        "repos/remove-collaborator",
        "repos/check-collaborator",
        "repos/merge",
        "repos/compare-commits",
        "repos/get-branch-protection",
        "repos/list-invitations",
        # The rest of git data. `create-ref` was missing outright, so nothing could
        # open a branch.
        "git/create-ref",
        "git/delete-ref",
        "git/list-matching-refs",
        "git/get-tree",
        "git/get-commit",
        "git/get-blob",
        "git/create-tag",
        # Releases beyond creating one.
        "repos/update-release",
        "repos/delete-release",
        "repos/generate-release-notes",
        "repos/list-release-assets",
        "repos/get-release-by-tag",
        # Who is in the organization.
        "orgs/get",
        "orgs/list-members",
        "teams/list",
        "teams/get-by-name",
        # Security alerts -- the thing a dev team most wants a bot watching.
        "code-scanning/list-alerts-for-repo",
        "code-scanning/get-alert",
        "dependabot/list-alerts-for-repo",
        "dependabot/get-alert",
        "secret-scanning/list-alerts-for-repo",
        # Reactions, so an agent can acknowledge without adding noise.
        "reactions/create-for-issue",
        "reactions/create-for-issue-comment",
        "reactions/create-for-pull-request-review-comment",
        # Search, notifications and the rate limit an agent should check before a
        # long run.
        "search/users",
        "search/commits",
        "rate-limit/get",
        "activity/list-notifications-for-authenticated-user",
        "activity/mark-notifications-as-read",
    ]
]

OVERRIDES = {
    # GitHub's official spec declares this operation's only response as a
    # bare 302 (no content type) -- pick_success_response only recognizes
    # 2xx codes, so without this override _resolve_response would fall back
    # to a generic non-binary schema. The executor follows the redirect
    # transparently either way; this only affects how the result is decoded.
    "repos/download-tarball-archive": {"binary_response": True},
    "repos/download-zipball-archive": {"binary_response": True},
    # `{ref}` here is a whole ref path (`heads/main`, `tags/v1`), not one
    # segment. Percent-encoding its slash makes GitHub answer 404 for every
    # real ref.
    "git/get-ref": {"multi_segment_path_params": ["ref"]},
    "git/update-ref": {"multi_segment_path_params": ["ref"]},
    # Same whole-ref-path problem, for the operations that read or delete one.
    "git/list-matching-refs": {"multi_segment_path_params": ["ref"]},
    "git/delete-ref": {"multi_segment_path_params": ["ref"]},
    # Logs and artifacts answer with a redirect to a signed URL, the same shape
    # as the archive downloads above.
    "actions/download-job-logs-for-workflow-run": {"binary_response": True},
    "actions/download-workflow-run-logs": {"binary_response": True},
    "actions/download-artifact": {"binary_response": True},
}

RAW_PASSTHROUGH_NAME = "github_http_request"
GRAPHQL_PASSTHROUGH_NAME = "github_graphql_request"

# Top-level property names and types, and nothing below that. Output schemas
# were 93% of this catalog entry -- `repos_get` alone was 72 KB, so one
# `describe_connector_operation` on it cost an agent roughly 18k tokens. What a
# model actually needs from an output schema is which fields come back; the
# shape of `owner.plan.collaborators` three levels down it can read off the
# response it already has.
_PROSE_KEYS = frozenset(
    {"description", "example", "examples", "title", "format", "default"}
)


def _prune_output_schema(node: object, depth: int = 0) -> object:
    if not isinstance(node, dict):
        return node
    pruned: dict = {}
    for key, value in node.items():
        if key in _PROSE_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            pruned[key] = {
                name: {"type": sub.get("type")} if isinstance(sub, dict) else {}
                for name, sub in value.items()
            }
        elif key == "items":
            pruned[key] = _prune_output_schema(value, depth + 1)
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            pruned[key] = [_prune_output_schema(value[0], depth + 1)] if value else []
        elif isinstance(value, dict):
            pruned[key] = _prune_output_schema(value, depth + 1)
        else:
            pruned[key] = value
    return pruned


def _token_kinds_by_route(spec: dict) -> dict[tuple[str, str], str]:
    """Whether an installation token can run each route, per GitHub's own spec.

    ``x-github.enabledForGitHubApps: false`` marks the endpoints only a
    user-to-server token reaches -- everything under `/user/...`, and gists.
    Read from the spec rather than hand-listed, so the answer cannot drift from
    what GitHub actually enforces. Keyed by (method, path), which is what the
    execution descriptor carries back.
    """
    kinds: dict[tuple[str, str], str] = {}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            enabled = (operation.get("x-github") or {}).get(
                "enabledForGitHubApps", True
            )
            kinds[(method.upper(), path)] = (
                "installation_ok" if enabled else "user_only"
            )
    return kinds


def _operation_to_static_entry(
    op: OpenAPIOperation, token_kinds: dict[tuple[str, str], str]
) -> dict:
    descriptor = op.execution or {}
    route = (
        str(descriptor.get("method", "")).upper(),
        str(descriptor.get("path", "")),
    )
    execution = {
        **descriptor,
        "github_token_kind": token_kinds.get(route, "installation_ok"),
    }
    entry: dict = {
        "name": op.public_name,
        "description": op.description,
        "execution": execution,
        "input_schema": op.input_schema,
    }
    if op.output_schema is not None:
        entry["output_schema"] = _prune_output_schema(op.output_schema)
    return entry


def build_static_operations() -> list[dict]:
    _ensure_spec()
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    operations = build_operation_descriptors(
        spec,
        server_url=SERVER_URL,
        allowlist=ALLOWLIST,
        overrides=OVERRIDES,
        default_headers=DEFAULT_HEADERS,
    )
    raw = build_raw_passthrough(
        "github",
        server_url=SERVER_URL,
        name=RAW_PASSTHROUGH_NAME,
        default_headers=DEFAULT_HEADERS,
    )
    token_kinds = _token_kinds_by_route(spec)
    entries = [_operation_to_static_entry(op, token_kinds) for op in [*operations, raw]]
    entries.append(_graphql_passthrough_entry())
    return entries


def _graphql_passthrough_entry() -> dict:
    """A GraphQL escape hatch, because Projects v2 has no REST surface at all.

    Projects is the one thing a dev team reaches for that GitHub never exposed
    over REST, so a catalog built only from the OpenAPI description can never
    reach it however many operations it lists.
    """
    return {
        "name": GRAPHQL_PASSTHROUGH_NAME,
        "description": (
            "Run a GraphQL query or mutation against GitHub's v4 API. Use this "
            "for Projects v2, which has no REST equivalent. Prefer a curated "
            "operation or github_http_request for anything REST can do."
        ),
        "execution": {
            "kind": "http",
            "mode": "raw",
            "method": "POST",
            "path": "/graphql",
            "server_url": "https://api.github.com",
            "default_headers": DEFAULT_HEADERS,
            "github_token_kind": "installation_ok",
        },
        "input_schema": {
            "type": "object",
            "title": GRAPHQL_PASSTHROUGH_NAME,
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The GraphQL query or mutation document.",
                },
                "variables": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Variables referenced by the document.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def _write_into_lemma_apps_config(static_operations: list[dict]) -> None:
    apps = json.loads(LEMMA_APPS_CONFIG_PATH.read_text(encoding="utf-8"))
    for app in apps:
        if app.get("name") == "github":
            app["static_operations"] = static_operations
            break
    else:
        raise SystemExit(
            "No 'github' entry found in lemma_apps_config.json — add the "
            "connector's non-operation fields (title, oauth2_config, "
            "system_oauth, ...) first, then re-run with --write."
        )
    LEMMA_APPS_CONFIG_PATH.write_text(
        json.dumps(apps, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Splice the generated static_operations directly into the "
        "existing 'github' entry in lemma_apps_config.json.",
    )
    args = parser.parse_args()

    static_operations = build_static_operations()

    if args.write:
        _write_into_lemma_apps_config(static_operations)
        print(
            f"Wrote {len(static_operations)} operations into "
            f"{LEMMA_APPS_CONFIG_PATH}'s 'github' entry."
        )
    else:
        print(json.dumps(static_operations, indent=2))


if __name__ == "__main__":
    main()
