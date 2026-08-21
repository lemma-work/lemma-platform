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
}

RAW_PASSTHROUGH_NAME = "github_http_request"


def _operation_to_static_entry(op: OpenAPIOperation) -> dict:
    entry: dict = {
        "name": op.public_name,
        "description": op.description,
        "execution": op.execution,
        "input_schema": op.input_schema,
    }
    if op.output_schema is not None:
        entry["output_schema"] = op.output_schema
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
    return [_operation_to_static_entry(op) for op in [*operations, raw]]


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
