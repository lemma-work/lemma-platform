"""Unit tests for pod-bundle API request/response schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.pod_bundle.api.schemas import ExportStartRequest, PublishStartRequest
from app.modules.pod_bundle.domain.state import PublishMode


def test_export_defaults_to_resources_only_no_data():
    """Row data is opt-in: an export request with no explicit flag carries only
    pod resources (with_data defaults to False), matching the CLI."""
    request = ExportStartRequest()
    assert request.with_data is False


def test_export_with_data_can_be_opted_in():
    """The rare seed/setup-data case is still expressible via an explicit flag."""
    request = ExportStartRequest(with_data=True)
    assert request.with_data is True


def test_export_data_tables_defaults_to_none():
    """Per-table seeding is opt-in: no tables selected unless named."""
    assert ExportStartRequest().data_tables is None


def test_export_data_tables_selects_specific_tables():
    """A caller can seed only specific setup tables without dumping the pod."""
    request = ExportStartRequest(data_tables=["settings", "roles"])
    assert request.data_tables == ["settings", "roles"]
    assert request.with_data is False


def test_export_with_files_defaults_off():
    """Pod file storage is opt-in, off by default (resources-only export)."""
    assert ExportStartRequest().with_files is False


def test_export_caps_are_conservative_and_shared():
    """Caps default to the shared lemma_pod_bundle values: 10k rows total, a
    20MB data pool (tables + files), and a separate 20MB app pool."""
    from lemma_pod_bundle.limits import (
        MAX_APPS_TOTAL_BYTES,
        MAX_DATA_TOTAL_BYTES,
        MAX_RECORDS_TOTAL,
    )

    from app.modules.pod_bundle.config import pod_bundle_settings

    assert pod_bundle_settings.pod_bundle_export_max_records_total == MAX_RECORDS_TOTAL
    assert MAX_RECORDS_TOTAL == 10_000
    assert (
        pod_bundle_settings.pod_bundle_export_max_files_total_bytes
        == MAX_DATA_TOTAL_BYTES
        == 20 * 1024 * 1024
    )
    assert (
        pod_bundle_settings.pod_bundle_export_max_apps_total_bytes
        == MAX_APPS_TOTAL_BYTES
        == 20 * 1024 * 1024
    )


def test_publish_requires_account_and_defaults_to_create():
    request = PublishStartRequest(repo_name="my-pod", account_id=uuid4())
    assert request.mode is PublishMode.CREATE

    with pytest.raises(ValidationError):
        PublishStartRequest(repo_name="my-pod")


@pytest.mark.parametrize("repo_name", ["space name", "owner/repo", ".", "..", "a" * 101])
def test_publish_rejects_invalid_repository_names(repo_name: str):
    with pytest.raises(ValidationError):
        PublishStartRequest(repo_name=repo_name, account_id=uuid4())
