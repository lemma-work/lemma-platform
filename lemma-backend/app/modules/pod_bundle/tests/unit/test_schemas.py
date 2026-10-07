"""Unit tests for pod-bundle API request/response schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.pod_bundle.api.schemas import (
    ExportStartRequest,
    ImportStartRequest,
    PublishStartRequest,
)
from app.modules.pod_bundle.domain.state import BundleSourceKind, PublishMode


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


def test_publish_accepts_an_organisation_owner():
    """`owner/name` is what makes an organisation reachable. Publishing used to
    resolve the connected user's own login and nothing else, so a pod could
    only ever land in a personal namespace."""
    request = PublishStartRequest(repo_name="acme-corp/my-pod", account_id=uuid4())
    assert request.repo_name == "acme-corp/my-pod"


def test_publish_requires_account_and_defaults_to_create():
    request = PublishStartRequest(repo_name="my-pod", account_id=uuid4())
    assert request.mode is PublishMode.CREATE

    with pytest.raises(ValidationError):
        PublishStartRequest(repo_name="my-pod")


@pytest.mark.parametrize(
    "repo_name", ["space name", "owner//repo", ".", "..", "a" * 101]
)
def test_publish_rejects_invalid_repository_names(repo_name: str):
    with pytest.raises(ValidationError):
        PublishStartRequest(repo_name=repo_name, account_id=uuid4())


# --- import start: templates -------------------------------------------------


def test_import_start_accepts_a_template_by_name():
    request = ImportStartRequest(kind="TEMPLATE", template="support-desk")
    assert request.kind is BundleSourceKind.TEMPLATE
    assert request.template == "support-desk"


@pytest.mark.parametrize(
    "template",
    [
        "../etc",
        "..",
        ".",
        "support-desk/../follow-ups",
        "support-desk/",
        "/support-desk",
        "support_desk",
        "Support-Desk",
        "-support",
        "support-desk\n",
        "",
        "a" * 64,
    ],
)
def test_import_start_refuses_a_template_name_that_is_not_a_plain_slug(template):
    """The name becomes a directory under ``templates/``, so anything that is
    not a lowercase slug -- a dot, a separator -- is refused before it gets
    near the filesystem."""
    with pytest.raises(ValidationError):
        ImportStartRequest(kind="TEMPLATE", template=template)


def test_import_start_template_kind_requires_a_name():
    with pytest.raises(ValidationError):
        ImportStartRequest(kind="TEMPLATE")


@pytest.mark.parametrize(
    "locating",
    [
        {"url": "https://github.com/acme/crm"},
        {"owner": "acme"},
        {"repo": "crm"},
        {"ref": "main"},
        {"account_id": str(uuid4())},
    ],
)
def test_import_start_template_refuses_fields_that_locate_a_bundle(locating):
    """Beside a template they would be ignored; refusing them says so."""
    with pytest.raises(ValidationError):
        ImportStartRequest(kind="TEMPLATE", template="support-desk", **locating)


@pytest.mark.parametrize("kind", ["URL", "GITHUB"])
def test_import_start_refuses_a_template_name_with_another_kind(kind):
    with pytest.raises(ValidationError):
        ImportStartRequest(
            kind=kind, url="https://github.com/acme/crm", template="support-desk"
        )


def test_plan_response_carries_the_bundle_description():
    from app.modules.pod_bundle.api.schemas import ImportPlanResponse
    from app.modules.pod_bundle.domain.state import ImportPlan

    plan = ImportPlan(format_version=3, description="Answers customers.")
    assert ImportPlanResponse.from_domain(plan).description == "Answers customers."


# --- partial apply -----------------------------------------------------------


def _import_state(status, *, committed_steps=(), statuses=()):
    from app.modules.pod_bundle.domain.state import (
        BundleSource,
        BundleSourceKind,
        ImportPlan,
        ImportState,
        PlanStep,
        StepAction,
        StepKind,
    )

    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind=BundleSourceKind.URL, url="https://lemma.test/b.zip"),
        status=status,
        committed_steps=list(committed_steps),
        plan=ImportPlan(
            format_version=1,
            steps=[
                PlanStep(
                    index=i,
                    kind=StepKind.TABLE,
                    name=f"t{i}",
                    action=StepAction.CREATE,
                    status=step_status,
                )
                for i, step_status in enumerate(statuses)
            ],
        ),
    )


def test_a_failed_import_reports_what_it_already_applied():
    """`committed_steps` is a bare list of integers; it never said the pod had
    been modified, nor that re-applying resumes rather than duplicating."""
    from app.modules.pod_bundle.api.schemas import ImportStatusResponse
    from app.modules.pod_bundle.domain.state import ImportStatus, StepStatus

    state = _import_state(
        ImportStatus.FAILED,
        committed_steps=[0, 1],
        statuses=[
            StepStatus.DONE,
            StepStatus.DONE,
            StepStatus.FAILED,
            StepStatus.PENDING,
        ],
    )
    partial = ImportStatusResponse.from_state(state).partial_apply

    assert partial is not None
    assert (partial.steps_applied, partial.steps_total) == (2, 4)
    # The failed step itself, not the one after it: apply resets it to PENDING.
    assert partial.resume_from_step == 2
    assert partial.resumable is True


def test_a_partially_cancelled_import_reports_the_same_but_is_not_resumable():
    """Apply refuses a cancelled job, so the pod keeps what landed and the rest
    has to be imported afresh."""
    from app.modules.pod_bundle.api.schemas import ImportStatusResponse
    from app.modules.pod_bundle.domain.state import ImportStatus, StepStatus

    state = _import_state(
        ImportStatus.PARTIALLY_CANCELLED,
        committed_steps=[0],
        statuses=[StepStatus.DONE, StepStatus.PENDING],
    )
    partial = ImportStatusResponse.from_state(state).partial_apply

    assert partial is not None
    assert partial.steps_applied == 1
    assert partial.resumable is False


@pytest.mark.parametrize(
    ("status", "committed"),
    [
        ("COMPLETED", [0, 1]),  # finished cleanly: nothing partial about it
        ("FAILED", []),  # failed before writing anything
        ("APPLYING", [0]),  # still running; not a terminal state to report
    ],
)
def test_no_partial_apply_when_the_pod_was_not_left_half_changed(status, committed):
    from app.modules.pod_bundle.api.schemas import ImportStatusResponse
    from app.modules.pod_bundle.domain.state import ImportStatus, StepStatus

    state = _import_state(
        ImportStatus(status),
        committed_steps=committed,
        statuses=[StepStatus.DONE, StepStatus.DONE],
    )
    assert ImportStatusResponse.from_state(state).partial_apply is None
