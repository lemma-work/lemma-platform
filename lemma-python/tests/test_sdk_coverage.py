"""Guards against the SDK drifting from the backend OpenAPI spec.

- Every generated function imported by a hand-written facade must still exist
  (covered implicitly by importing lemma_sdk.resources).
- Reports backend operations that no facade exposes, so additions are a
  conscious decision instead of silent gaps.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1] / "lemma_sdk"
RESOURCES_DIR = SDK_ROOT / "resources"
SPEC_PATH = SDK_ROOT / "openapi_spec.json"

# Backend operations intentionally not wrapped by a facade yet. Remove entries
# when a facade adopts them; additions here should be deliberate.
KNOWN_UNEXPOSED_PREFIXES = (
    "auth.",            # CLI auth flows use lemma_sdk.auth, not generated client
    "billing",          # filtered out of the client spec
    "public.",          # unauthenticated public endpoints
    "webhook",          # inbound webhooks, not client-callable
    "surface.webhook",  # inbound surface webhooks
    "icon.",            # asset serving
    "admin",
    "scheduler.job",    # internal scheduler ops
    "usage.",
    "workspace.",       # workspace runtime is driven by the backend
    "channel.",
    "app.public",
    "health_check",     # liveness probe
    # OAuth/consent browser callbacks — never called by a client
    "connector.oauth.callback",
    "agent.surface.teams_admin_consent_callback",
    # Facades use custom multipart/streaming helpers instead of these
    "file.upload",
    "app.bundle.upload",
    "app.asset.",
    "file.converted.",
    # Pod bundle export/import/publish: job-triggering, multipart-upload, and
    # SSE endpoints consumed by the frontend (TS SDK) and the import wizard. The
    # CLI imports client-side via the per-resource facades, not these; a Python
    # facade is a deliberate follow-up.
    "pod.bundle.",
    # Membership/governance surface not yet exposed in CLI/SDK facades
    "org.invitation.",
    "org.member.",
    "org.join_auto_join",
    "org.update",            # owner-only join-policy/name edit
    "org.slug_availability",
    "org.suggested",
    "pod.join",              # self-join + join-request governance
    "pod.join_request.",
    "pod.member.",
    "pod.permissions.",
    "pod.resource_access.",
    "pod.role",
    "pod.config.get",
    # Runtime/visualization extras pending facade adoption
    "connector.skill.get",
    "connector.status.get",
    "user.current.get",     # facade uses user.profile endpoints
    "workflow.visualize",
    "workflow.run.visualize",
    "workflow.run.waiting_assigned_to_me",
    # Widget/app unification endpoints pending facade adoption
    "app.create_from_widget",
    "widget.embed_token",
)


def _spec_operation_ids() -> set[str]:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for path_item in spec["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict) and operation.get("operationId"):
                ids.add(operation["operationId"])
    return ids


def _python_name(operation_id: str) -> str:
    """Mirror openapi-python-client's module naming for an operationId."""
    return re.sub(r"[^a-z0-9]+", "_", operation_id.lower()).strip("_")


def _facade_imported_functions() -> set[str]:
    imported: set[str] = set()
    for path in RESOURCES_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "openapi_client.api" in node.module:
                for alias in node.names:
                    imported.add(alias.name)
    return imported


def test_facade_imports_resolve():
    """Any rename in the regenerated client must fail loudly, not at call time."""
    import lemma_sdk  # noqa: F401
    from lemma_sdk import resources  # noqa: F401


def test_facade_imports_exist_in_spec():
    spec_modules = {_python_name(op) for op in _spec_operation_ids()}
    imported = _facade_imported_functions()
    stale = sorted(name for name in imported if name not in spec_modules)
    assert not stale, (
        "Facades import generated functions that no longer exist in the spec "
        f"(regenerate the SDK and update lemma_sdk/resources): {stale}"
    )


def test_report_unexposed_operations():
    """Fails when NEW backend ops appear that no facade wraps and no allowlist entry covers."""
    spec_ops = _spec_operation_ids()
    imported = _facade_imported_functions()
    unexposed = sorted(
        op
        for op in spec_ops
        if _python_name(op) not in imported
        and not any(op.startswith(prefix) for prefix in KNOWN_UNEXPOSED_PREFIXES)
    )
    assert not unexposed, (
        "Backend operations with no SDK facade coverage. Either wrap them in "
        "lemma_sdk/resources or add them to KNOWN_UNEXPOSED_PREFIXES deliberately: "
        f"{unexposed}"
    )
