"""The rules file processing is decided by, each as a pure function.

Which failures earn a free retry, which writes reprocess a file, what a dead
item tells a person, and where a revision's bytes live.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from app.core.embeddings.embeddings import EmbeddingProviderError
from app.core.embeddings.local_embedder import EmbeddingModelUnavailableError
from app.core.infrastructure.work import UnavailableEvidence
from app.modules.datastore.domain.errors import ExtractorFailedOnDocument
from app.modules.datastore.domain.events import DatastoreFileCreatedEvent
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileStatus,
    ProcessingRequest,
)
from app.modules.datastore.events.file_work import dead_message
from app.modules.datastore.infrastructure.file_chunk_repository import parent_path_of
from app.modules.datastore.infrastructure.kreuzberg_circuit import (
    KreuzbergCircuitOpen,
)
from app.modules.datastore.infrastructure.kreuzberg_helper import (
    KreuzbergRateLimited,
    KreuzbergUnhealthy,
    KreuzbergUnreachable,
)
from app.modules.datastore.infrastructure.storage_paths import (
    OBJECT_LAYOUT,
    content_keys,
    fallback_keys,
    readable_keys,
)
from app.modules.datastore.services.processing_run import unavailable_for

pytestmark = pytest.mark.unit


def _provider_error(cause: Exception) -> EmbeddingProviderError:
    try:
        raise EmbeddingProviderError("Failed to get embeddings") from cause
    except EmbeddingProviderError as exc:
        return exc


def _status(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://embeddings.example.test/v1")
    return httpx.HTTPStatusError(
        "bad", request=request, response=httpx.Response(code, request=request)
    )


@pytest.mark.parametrize(
    ("error", "evidence"),
    [
        (KreuzbergUnreachable("refused"), UnavailableEvidence.CONNECTION_REFUSED),
        (KreuzbergUnhealthy("down"), UnavailableEvidence.HEALTH_CHECK_FAILING),
        (KreuzbergCircuitOpen("open"), UnavailableEvidence.CIRCUIT_OPEN),
        (KreuzbergRateLimited("429"), UnavailableEvidence.RATE_LIMITED),
        (
            EmbeddingModelUnavailableError("local embedding model is not available"),
            UnavailableEvidence.HEALTH_CHECK_FAILING,
        ),
        (_provider_error(_status(503)), UnavailableEvidence.HEALTH_CHECK_FAILING),
        (_provider_error(_status(502)), UnavailableEvidence.HEALTH_CHECK_FAILING),
        (_provider_error(_status(429)), UnavailableEvidence.RATE_LIMITED),
        (
            _provider_error(httpx.ConnectError("refused")),
            UnavailableEvidence.CONNECTION_REFUSED,
        ),
    ],
)
def test_only_evidence_of_an_outage_is_unavailable(error, evidence) -> None:
    unavailable = unavailable_for(error)
    assert unavailable is not None
    assert unavailable.evidence is evidence


@pytest.mark.parametrize(
    "error",
    [
        ExtractorFailedOnDocument("status 500: panicked"),
        _provider_error(_status(400)),
        RuntimeError("Extractor returned an empty result set"),
        ValueError("1 vectors for 2 chunks"),
    ],
)
def test_a_failure_on_the_document_is_charged(error) -> None:
    assert unavailable_for(error) is None


def test_a_rate_limit_keeps_the_servers_retry_after() -> None:
    unavailable = unavailable_for(KreuzbergRateLimited("429", retry_after_seconds=12))
    assert unavailable is not None and unavailable.retry_after is not None
    assert unavailable.retry_after.total_seconds() == 12


def test_a_dead_item_tells_the_person_why_in_its_own_words() -> None:
    assert dead_message("attempts_exhausted", "RuntimeError: x").startswith(
        "document_unprocessable: RuntimeError"
    )
    assert dead_message("unavailable_exhausted", None).startswith(
        "extractor_unavailable:"
    )
    assert dead_message("lease_losses_exhausted", None).startswith("worker_lost:")


def _file(**overrides) -> DatastoreFileEntity:
    values = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "path": "/docs/a.pdf",
        "name": "a.pdf",
        "mime_type": "application/pdf",
        "content_sha256": "a" * 64,
        "status": FileStatus.COMPLETED,
    }
    values.update(overrides)
    return DatastoreFileEntity(**values)


@pytest.mark.parametrize(
    "status",
    [
        FileStatus.COMPLETED,
        FileStatus.PENDING,
        FileStatus.PROCESSING,
        FileStatus.FAILED_PERMANENT,
    ],
)
def test_the_same_bytes_again_change_nothing(status: FileStatus) -> None:
    file = _file(status=status)
    file.mark_content_updated(previous_sha256="a" * 64)
    assert file.status is status
    assert file.processing_request is None


def test_new_bytes_reopen_even_a_permanent_failure_with_a_debounce() -> None:
    file = _file(status=FileStatus.FAILED_PERMANENT, content_sha256="b" * 64)
    file.mark_content_updated(previous_sha256="a" * 64)
    assert file.status is FileStatus.PENDING
    assert file.processing_request is ProcessingRequest.CONTENT


def test_changed_inputs_reprocess_the_same_bytes() -> None:
    file = _file()
    file.mark_content_updated(previous_sha256="a" * 64, inputs_changed=True)
    assert file.processing_request is ProcessingRequest.CONTENT


def test_echoing_search_enabled_does_not_requeue_an_indexed_file() -> None:
    file = _file(search_enabled=True)
    file.set_search_enabled(True)
    assert file.status is FileStatus.COMPLETED
    assert file.processing_request is None


def test_a_retry_asks_for_processing_without_a_debounce() -> None:
    file = _file(status=FileStatus.FAILED_PERMANENT)
    assert file.mark_retry_requested()
    assert file.processing_request is ProcessingRequest.RETRY


def test_object_layout_keys_follow_the_digest_not_the_path() -> None:
    file = _file(storage_layout=OBJECT_LAYOUT, indexed_sha256="0" * 64)
    moved = file.model_copy(update={"path": "/archive/a.pdf"})
    assert content_keys(file) == content_keys(moved)
    base = f"pods/{file.pod_id}/objects/{file.id}"
    assert content_keys(file).original == f"{base}/{'a' * 64}/content"
    # Readers keep the indexed revision's conversion until the new one lands.
    assert readable_keys(file).markdown == f"{base}/{'0' * 64}/derived/document.md"
    assert readable_keys(file).original == f"{base}/{'a' * 64}/content"
    other = fallback_keys(file)
    assert other is not None and other.original.endswith("/files/docs/a.pdf")


def test_path_layout_keys_are_the_legacy_ones() -> None:
    file = _file()
    assert content_keys(file).original == f"pods/{file.pod_id}/files/docs/a.pdf"
    assert content_keys(file).markdown == (
        f"pods/{file.pod_id}/files/docs/.a.pdf/document.md"
    )


@pytest.mark.parametrize(
    ("path", "parent"),
    [("/a.md", "/"), ("/docs/a.md", "/docs"), ("/docs/x/a.md", "/docs/x")],
)
def test_parent_path(path: str, parent: str) -> None:
    assert parent_path_of(path) == parent


def test_lemmas_own_database_being_unavailable_is_not_the_documents_fault() -> None:
    from sqlalchemy.exc import OperationalError

    from app.core.infrastructure.work import was_database_unavailable
    from app.core.infrastructure.work.retry import describe_error

    class _TooMany(Exception):
        sqlstate = "53300"

    unavailable = unavailable_for(OperationalError("SELECT", {}, _TooMany("full")))
    assert unavailable is not None
    assert unavailable.evidence is UnavailableEvidence.CONNECTION_REFUSED
    message = dead_message("unavailable_exhausted", describe_error(unavailable))
    assert message.startswith("database_unavailable:")
    assert "nothing is wrong with the document" in message
    assert was_database_unavailable(describe_error(unavailable))
    assert dead_message("unavailable_exhausted", "Unavailable: KreuzbergUnreachable")
    assert "extractor" in dead_message("unavailable_exhausted", None)


def test_a_created_file_says_how_big_it_is() -> None:
    """Its size is on the row, not in its metadata, which is where a reader of
    the event used to look -- and find nothing."""
    file = _file(size_bytes=123_456, metadata={"source": "upload"})
    file.mark_created(uuid4())
    (created,) = [
        e for e in file.collect_events() if isinstance(e, DatastoreFileCreatedEvent)
    ]
    assert created.size_bytes == 123_456
