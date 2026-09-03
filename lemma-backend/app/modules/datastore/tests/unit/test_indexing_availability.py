"""What a person is told when the deployment, not their document, is broken.

`PS-DATA-021` settled this for querying: a facility the install never
provisioned must report itself, rather than failing each request as though the
caller had made a mistake. Ingestion had no equivalent, so a fresh install with
no embedding provider answered every upload with
``"ProgrammingError: document processing failed"`` — the same string a corrupt
PDF gets, naming nothing anyone could change.

The exceptions here are the real ones: the OpenAI-compatible embedder's own
refusal when no key is configured, the wording it wraps every transport failure
in, and PostgreSQL's catalogue messages for an absent pgvector.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.services.search.indexing_availability import (
    missing_indexing_facility,
    sanitize_processing_error,
)


class _DriverError(Exception):
    """A DBAPIError-shaped exception: the cause hides behind ``orig``."""

    def __init__(self, summary: str, orig: Exception) -> None:
        super().__init__(summary)
        self.orig = orig


UNCONFIGURED_EMBEDDER = RuntimeError(
    "OpenAI-compatible embeddings require LEMMA_OPENAI_API_KEY to be set "
    "(or set EMBEDDING_PROVIDER=local to use offline embeddings)."
)

UNREACHABLE_EMBEDDER = Exception(
    "Failed to get embeddings for batch starting at index 0: "
    "[Errno 61] Connection refused"
)

NO_PGVECTOR = _DriverError(
    "(psycopg.errors.UndefinedObject) ...",
    Exception('type "vector" does not exist\nLINE 6: embedding vector(768)'),
)

NO_PERMISSION_TO_INSTALL_PGVECTOR = _DriverError(
    "(psycopg.errors.InsufficientPrivilege) ...",
    Exception('permission denied to create extension "vector"'),
)


@pytest.mark.parametrize(
    ("exc", "facility"),
    [
        (UNCONFIGURED_EMBEDDER, "embedding_provider"),
        (UNREACHABLE_EMBEDDER, "embedding_provider"),
        (NO_PGVECTOR, "vector_extension"),
        (NO_PERMISSION_TO_INSTALL_PGVECTOR, "vector_extension"),
    ],
)
def test_a_deployment_failure_is_recognised_as_one(exc, facility):
    assert missing_indexing_facility(exc) == facility


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("Embedding provider returned 3 vectors for 4 chunks"),
        RuntimeError("could not parse page 4"),
        Exception("stream ended unexpectedly"),
    ],
)
def test_a_document_failure_is_not_mistaken_for_one(exc):
    assert missing_indexing_facility(exc) is None


@pytest.mark.parametrize(
    ("exc", "names"),
    [
        (UNCONFIGURED_EMBEDDER, "EMBEDDING_PROVIDER"),
        (UNREACHABLE_EMBEDDER, "EMBEDDING_PROVIDER"),
        (NO_PGVECTOR, "pgvector"),
    ],
)
def test_the_message_names_the_setting_an_operator_has_to_change(exc, names):
    message = sanitize_processing_error(exc)

    assert names in message
    assert "not a problem with the document" in message


def test_the_deployment_message_is_ours_and_not_the_provider_s():
    """The summary exists to keep provider text out of a served-back field.

    A facility-absent message must therefore be written here, not lifted from
    the exception -- otherwise recognising the case would reopen the leak the
    sanitising was for.
    """
    leaky = Exception(
        "Failed to get embeddings for batch starting at index 0: "
        "POST https://embeddings.example/v1/embeddings?token=CANARY_SECRET"
    )

    message = sanitize_processing_error(leaky)

    assert "CANARY_SECRET" not in message
    assert "embeddings.example" not in message


def test_an_ordinary_failure_still_says_only_its_class():
    assert (
        sanitize_processing_error(RuntimeError("page 4: /objects/abc.pdf"))
        == "RuntimeError: document processing failed"
    )
