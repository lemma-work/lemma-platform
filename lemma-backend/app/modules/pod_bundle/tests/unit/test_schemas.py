"""Unit tests for pod-bundle API request/response schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.pod_bundle.api.schemas import ExportStartRequest, PublishStartRequest
from app.modules.pod_bundle.domain.state import PublishMode


def test_export_defaults_to_resources_only():
    """Naming nothing exports the pod's shape alone — no rows, no files."""
    request = ExportStartRequest()
    assert request.data_tables is None
    assert request.file_folders is None


def test_export_selects_tables_and_folders_by_name():
    """Both dimensions are chosen one name at a time."""
    request = ExportStartRequest(
        data_tables=["settings", "roles"], file_folders=["/reports"]
    )
    assert request.data_tables == ["settings", "roles"]
    assert request.file_folders == ["/reports"]


@pytest.mark.parametrize("field", ["with_data", "with_files"])
def test_export_refuses_the_old_everything_switches(field):
    """The blanket flags are gone, not merely defaulted off. A caller still
    sending one is asking for every table's rows or the whole file tree, and
    silently accepting it as "export nothing" would be the same surprise in the
    opposite direction — so the request is rejected outright."""
    with pytest.raises(ValidationError):
        ExportStartRequest(**{field: True})


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


@pytest.mark.parametrize(
    "repo_name", ["space name", "owner/repo", ".", "..", "a" * 101]
)
def test_publish_rejects_invalid_repository_names(repo_name: str):
    with pytest.raises(ValidationError):
        PublishStartRequest(repo_name=repo_name, account_id=uuid4())
