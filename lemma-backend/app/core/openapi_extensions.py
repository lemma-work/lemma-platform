"""x-lemma OpenAPI extensions — the metadata spine for SDK codegen (Wave 3, CG-4).

A single function, :func:`apply_lemma_metadata`, stamps an ``x-lemma`` block onto
every API operation in the OpenAPI schema. The SDK generator (CG-3) reads those blocks
to emit L2 facades and TanStack-Query hooks with a correct invalidation graph.

Most fields are *derived* from the operationId (``resource.verb`` convention) and the
HTTP method, so coverage is automatic. The :data:`OVERRIDES` registry only carries the
parts a heuristic cannot know — chiefly cross-resource ``invalidates`` edges.

This module is intentionally dependency-free (stdlib only) so it can run inside
``custom_openapi()`` (live ``/openapi.json``), in the committed-spec stamper, and in
unit tests without importing the app or a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

HTTP_METHODS = {"get", "put", "post", "delete", "patch"}

# Query parameters that signal cursor/offset pagination on a list endpoint.
PAGINATION_PARAMS = {
    "limit",
    "offset",
    "cursor",
    "page_token",
    "page",
    "after",
    "before",
}

# operationIds that are not resource operations and intentionally carry no x-lemma
# (utility endpoints the SDK never generates against).
SKIP_OPERATION_IDS = {"health_check_health_get"}

# Verb -> result-shape heuristics (overridable per-operation in OVERRIDES).
COLLECTION_VERBS = {"list", "list_mine", "search"}
VOID_VERBS = {"delete", "remove", "revoke", "stop", "archive", "bulk_delete"}

# Hand-authored refinements keyed by operationId. Auto-derivation already covers
# resource/verb/kind/result/paginates; this registry adds cross-resource `invalidates`
# edges and corrects any field the heuristic gets wrong. A mutation absent from this
# map defaults to invalidating its own resource (see `_default_invalidates`).
#
# Records is the worked vertical slice for Phase 1: every record mutation busts the
# `record` resource (lists, single records, and the relational/joined views are all
# `record` queries). Other resources currently rely on the own-resource default; cross-
# resource edges get filled here as each slice is migrated.
OVERRIDES: dict[str, dict[str, Any]] = {
    "record.create": {"invalidates": ["record"]},
    "record.update": {"invalidates": ["record"]},
    "record.delete": {"invalidates": ["record"]},
    "record.bulk_create": {"invalidates": ["record"]},
    "record.bulk_update": {"invalidates": ["record"]},
    "record.bulk_delete": {"invalidates": ["record"]},
}


def split_operation_id(operation_id: str) -> tuple[str | None, str]:
    """Return ``(resource, verb)``. ``agent.permissions.get`` -> ``("agent.permissions", "get")``.

    An operationId with no dot (e.g. an auto-named utility route) yields ``(None, id)``.
    """
    resource, sep, verb = operation_id.rpartition(".")
    if not sep:
        return None, verb
    return resource, verb


def _kind(method: str) -> str:
    """query for reads, mutation for writes — keyed off the HTTP method, not the verb name."""
    return "query" if method.lower() == "get" else "mutation"


def _result(verb: str) -> str:
    if verb in COLLECTION_VERBS:
        return "collection"
    if verb in VOID_VERBS:
        return "void"
    return "entity"


def _paginates(method: str, operation: Mapping[str, Any]) -> bool:
    if method.lower() != "get":
        return False
    for param in operation.get("parameters", []) or []:
        if (
            isinstance(param, Mapping)
            and str(param.get("name", "")).lower() in PAGINATION_PARAMS
        ):
            return True
    return False


def _default_invalidates(resource: str | None) -> list[str]:
    return [resource] if resource else []


def lemma_metadata_for(
    operation_id: str,
    method: str,
    operation: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build the x-lemma block for one operation, or ``None`` if it is intentionally skipped."""
    if operation_id in SKIP_OPERATION_IDS:
        return None

    resource, verb = split_operation_id(operation_id)
    if resource is None:
        # Non-resource operationId we have not explicitly skipped — surface it rather
        # than silently mislabel it. validate_metadata_coverage() reports these.
        return None

    kind = _kind(method)
    override = OVERRIDES.get(operation_id, {})

    meta: dict[str, Any] = {
        "resource": override.get("resource", resource),
        "verb": override.get("verb", verb),
        "kind": override.get("kind", kind),
        "result": override.get("result", _result(verb)),
        "paginates": override.get("paginates", _paginates(method, operation)),
    }
    if meta["kind"] == "mutation":
        meta["invalidates"] = override.get(
            "invalidates", _default_invalidates(meta["resource"])
        )
    return meta


def _iter_operations(schema: Mapping[str, Any]):
    paths = schema.get("paths")
    if not isinstance(paths, Mapping):
        return
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
        for method, operation in item.items():
            if method.lower() in HTTP_METHODS and isinstance(operation, dict):
                yield path, method, operation


def apply_lemma_metadata(schema: dict[str, Any]) -> dict[str, Any]:
    """Stamp ``x-lemma`` onto every resource operation in ``schema`` (mutated in place)."""
    for _path, method, operation in _iter_operations(schema):
        operation_id = operation.get("operationId")
        if not operation_id:
            continue
        meta = lemma_metadata_for(operation_id, method, operation)
        if meta is not None:
            operation["x-lemma"] = meta
        else:
            operation.pop("x-lemma", None)
    return schema


def validate_metadata_coverage(schema: Mapping[str, Any]) -> list[str]:
    """Return human-readable problems; an empty list means the metadata is complete and sound.

    Run in CI (Phase 1 acceptance gate): every operation is either skipped or annotated,
    and every ``invalidates`` edge names a resource that actually exists.
    """
    problems: list[str] = []
    known_resources: set[str] = set()

    annotated: list[tuple[str, dict[str, Any]]] = []
    for _path, method, operation in _iter_operations(schema):
        operation_id = operation.get("operationId")
        if not operation_id:
            problems.append(f"{method.upper()} {_path}: operation has no operationId")
            continue
        if operation_id in SKIP_OPERATION_IDS:
            continue
        resource, _verb = split_operation_id(operation_id)
        if resource is None:
            problems.append(
                f"{operation_id}: non-resource operationId is neither dotted nor in SKIP_OPERATION_IDS"
            )
            continue
        meta = operation.get("x-lemma")
        if not isinstance(meta, dict):
            problems.append(f"{operation_id}: missing x-lemma metadata")
            continue
        known_resources.add(meta.get("resource", resource))
        annotated.append((operation_id, meta))

    for operation_id, meta in annotated:
        for target in meta.get("invalidates", []) or []:
            if target not in known_resources:
                problems.append(
                    f"{operation_id}: invalidates unknown resource '{target}'"
                )
    return problems
