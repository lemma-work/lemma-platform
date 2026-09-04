"""What a document's ``last_processing_error`` is allowed to say.

Two different failures end up in the same field and want opposite treatment.

A failure *about the document* — a corrupt PDF, an extractor that choked on a
file — must be summarised to nothing but its class. Provider bodies, object
keys, SQL, URLs and credentials all appear in exception messages, and this
string is served back through the API.

A failure *about the deployment* — no embedding provider is configured, the
endpoint is unreachable, the ``vector`` extension is absent — is not the
person's document and never will be. Collapsing it to
``"ProgrammingError: document processing failed"`` is how a whole-install
misconfiguration presents itself as "every document I upload says it failed",
with nothing anywhere naming the setting that is missing. `PS-DATA-021` already
decided this for the query facility, where a missing datastore query role now
answers 503 with an explanation instead of blaming the caller's SQL; ingestion
had no equivalent.

So facility-absent failures are recognised here and answered with a message
this module wrote — never with the provider's own text, which is exactly the
material the summary exists to keep out.

Recognition is by message shape, like ``_query_facility_is_absent``, because
the causes arrive as whatever type the provider client raised: the
OpenAI-compatible embedder turns every transport failure into a bare
``Exception``, and the pgvector cases arrive as driver errors naming no table
and no column. What is matched is the platform's own wording where possible
(the embedder raises its own "requires LEMMA_OPENAI_API_KEY" message) and
PostgreSQL's stable catalogue wording otherwise.

A file that fails this way still spends its processing attempt and reaches
FAILED_PERMANENT, deliberately: unlike an extractor blip, an unconfigured
embedding provider is a standing state, so refunding the attempt would mean an
unbounded re-drive loop with no terminal status and nothing recorded anywhere.
Failing with a message that names the setting stops, and says why.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger

logger = get_logger(__name__)

_EMBEDDING_UNAVAILABLE = (
    "Document indexing is not available on this deployment: its embedding "
    "provider is not configured or cannot be reached. Set EMBEDDING_PROVIDER "
    "(and the credentials for the provider you choose) and re-upload. This is "
    "a deployment setting, not a problem with the document."
)

_VECTOR_EXTENSION_UNAVAILABLE = (
    "Document indexing is not available on this deployment: the PostgreSQL "
    "'vector' extension is not installed and this database role cannot create "
    "it. Install pgvector, or grant the datastore role permission to create "
    "the extension. This is a deployment setting, not a problem with the "
    "document."
)

# The embedder's own refusal when no credentials are configured, and the
# wording it wraps every transport failure in. Both are strings this platform
# writes, which is why matching them is not guesswork about a vendor's text.
_EMBEDDING_MARKERS = (
    "lemma_openai_api_key",
    "embedding_provider=local",
    "failed to get embeddings",
)

# PostgreSQL's own catalogue wording. `type "vector" does not exist` is what a
# CREATE TABLE against a pod schema says when pgvector never installed; the
# other two are what CREATE EXTENSION says when the binary is absent or the
# role may not install it.
_VECTOR_MARKERS = (
    'type "vector" does not exist',
    'extension "vector" is not available',
    "permission denied to create extension",
    "could not open extension control file",
)


def missing_indexing_facility(exc: BaseException) -> str | None:
    """Name the absent facility behind an indexing failure, or ``None``.

    ``None`` means the failure is about this document, so the caller keeps the
    summarised form.
    """
    raw = f"{exc} {getattr(exc, 'orig', '')}".lower()
    if any(marker in raw for marker in _VECTOR_MARKERS):
        return "vector_extension"
    if any(marker in raw for marker in _EMBEDDING_MARKERS):
        return "embedding_provider"
    return None


def sanitize_processing_error(exc: Exception) -> str:
    """The user-facing string persisted as a file's ``last_processing_error``.

    A document-level failure yields only its class and a stable summary;
    detailed diagnostics belong in redacted structured telemetry. A
    facility-absent failure yields the message naming the setting, because that
    is the only thing anyone can act on.
    """
    facility = missing_indexing_facility(exc)
    if facility == "vector_extension":
        return _VECTOR_EXTENSION_UNAVAILABLE
    if facility == "embedding_provider":
        return _EMBEDDING_UNAVAILABLE
    return f"{type(exc).__name__}: document processing failed"


def warn_if_a_facility_is_absent(exc: Exception, *, pod_id: UUID) -> None:
    """Say once, where an operator looks, that the deployment is the cause.

    The processing failure itself is logged at debug, which LOG_LEVEL=INFO
    drops before formatting, so a whole-install misconfiguration otherwise
    exists nowhere but inside each affected file's ``last_processing_error``.
    """
    facility = missing_indexing_facility(exc)
    if facility is not None:
        logger.warning(
            "datastore.indexing_availability.indexing_facility_absent.degraded",
            facility=facility,
            pod_id=str(pod_id),
        )
