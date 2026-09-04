"""Unit tests for the x-lemma metadata spine (Wave 3, CG-4).

These run against the committed OpenAPI spec — the same artifact the SDK generator reads
— so the test is the Phase 1 acceptance gate: 100% coverage, sound invalidation edges,
and correct derivation for the records vertical slice.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.core.openapi_extensions import (
    apply_lemma_metadata,
    lemma_metadata_for,
    split_operation_id,
    validate_metadata_coverage,
)

SPEC_PATH = (
    Path(__file__).resolve().parents[4]
    / "lemma-python"
    / "lemma_sdk"
    / "openapi_spec.json"
)


@pytest.fixture(scope="module")
def annotated_spec() -> dict:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return apply_lemma_metadata(spec)


def _operations(spec: dict):
    for item in spec.get("paths", {}).values():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method in {"get", "put", "post", "delete", "patch"} and isinstance(
                operation, dict
            ):
                yield method, operation


def test_split_operation_id():
    assert split_operation_id("record.list") == ("record", "list")
    assert split_operation_id("agent.permissions.get") == ("agent.permissions", "get")
    assert split_operation_id("record.bulk_create") == ("record", "bulk_create")
    assert split_operation_id("health_check_health_get") == (
        None,
        "health_check_health_get",
    )


def test_every_resource_operation_is_annotated(annotated_spec: dict):
    assert validate_metadata_coverage(annotated_spec) == []


def test_annotated_blocks_are_well_formed(annotated_spec: dict):
    seen = 0
    for _method, operation in _operations(annotated_spec):
        meta = operation.get("x-lemma")
        if meta is None:  # intentionally skipped (e.g. /health)
            continue
        seen += 1
        assert meta["kind"] in {"query", "mutation"}
        assert meta["result"] in {"collection", "entity", "void"}
        assert isinstance(meta["paginates"], bool)
        assert meta["resource"]
        assert meta["verb"]
        if meta["kind"] == "mutation":
            assert isinstance(meta["invalidates"], list)
    assert seen > 150  # the spec has ~190 ops; guard against a no-op apply


def test_records_slice_derivation(annotated_spec: dict):
    by_id = {op.get("operationId"): op for _m, op in _operations(annotated_spec)}

    record_list = by_id["record.list"]["x-lemma"]
    assert record_list == {
        "resource": "record",
        "verb": "list",
        "kind": "query",
        "result": "collection",
        "paginates": True,
    }

    record_create = by_id["record.create"]["x-lemma"]
    assert record_create["kind"] == "mutation"
    assert record_create["result"] == "entity"
    assert record_create["invalidates"] == ["record"]

    record_delete = by_id["record.delete"]["x-lemma"]
    assert record_delete["result"] == "void"
    assert record_delete["invalidates"] == ["record"]


def test_apply_is_idempotent(annotated_spec: dict):
    once = copy.deepcopy(annotated_spec)
    twice = apply_lemma_metadata(copy.deepcopy(annotated_spec))
    assert once == twice


def test_get_without_pagination_params_does_not_paginate():
    operation = {"parameters": [{"name": "pod_id"}, {"name": "record_id"}]}
    meta = lemma_metadata_for("record.get", "get", operation)
    assert meta["paginates"] is False
    assert meta["kind"] == "query"
    assert "invalidates" not in meta
