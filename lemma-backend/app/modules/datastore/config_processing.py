"""Document-processing budgets, limits and the storage-layout switch, and how
often what a pod owns is measured.

Split from ``config.py`` to keep it under the size ratchet; ``DatastoreSettings``
inherits these, so the environment variables and ``datastore_settings`` are
unchanged.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class DocumentWorkSettings(BaseSettings):
    # What a pod owns: its measure is read on this interval into the week's row
    # of `pod_outcome_weeks`, and a week keeps its last reading.
    datastore_outcome_read_interval_minutes: int = Field(
        default=60,
        ge=5,
        description=(
            "How often every pod's measure of what it owns is read into that "
            "week's row. A week keeps its last reading, so this is also how long "
            "before the week ends its closing number may have been read."
        ),
    )
    document_processing_max_attempts: int = Field(
        default=3,
        description=(
            "Attempts a document gets for failures caused by the document itself "
            "-- including an extractor 5xx, timeout or 413 on it while the "
            "extractor's health check passes -- before it is FAILED_PERMANENT. "
            "An extractor that is down is not charged; see "
            "``document_processing_unavailable_budget_seconds``. Env: "
            "``DOCUMENT_PROCESSING_MAX_ATTEMPTS``."
        ),
    )
    document_processing_unavailable_budget_seconds: int = Field(
        default=86_400,
        description=(
            "How long a document may wait on an extractor or embedding provider "
            "that is down (connection refused, health check failing, circuit "
            "open because of other files) before it is FAILED_PERMANENT. Waiting "
            "is not charged as an attempt and backs off up to an hour. Env: "
            "``DOCUMENT_PROCESSING_UNAVAILABLE_BUDGET_SECONDS``."
        ),
    )
    document_processing_max_pages: int = Field(
        default=2_000,
        description=(
            "PDFs with more pages than this are FAILED_PERMANENT (too_large) "
            "before the extractor is asked. The page count comes from a pypdfium "
            "probe of the stored original. 0 disables. Env: "
            "``DOCUMENT_PROCESSING_MAX_PAGES``."
        ),
    )
    document_processing_max_ocr_pages: int = Field(
        default=500,
        description=(
            "Scanned PDFs (those the OCR probe would force through OCR) with more "
            "pages than this are FAILED_PERMANENT (too_large): OCR is the "
            "extractor's most expensive path. Only applies with OCR enabled. 0 "
            "disables. Env: ``DOCUMENT_PROCESSING_MAX_OCR_PAGES``."
        ),
    )
    document_processing_max_chunks: int = Field(
        default=20_000,
        description=(
            "Documents expected to produce, or producing, more search chunks "
            "than this are FAILED_PERMANENT (too_large) before they are embedded. "
            "The estimate is checked before extraction from the size and page "
            "count, the actual count after. 0 disables. Env: "
            "``DOCUMENT_PROCESSING_MAX_CHUNKS``."
        ),
    )
    document_processing_retry_cooldown_seconds: int = Field(
        default=60,
        description=(
            "A person may ask for one failed document to be processed again at "
            "most once per this many seconds. Env: "
            "``DOCUMENT_PROCESSING_RETRY_COOLDOWN_SECONDS``."
        ),
    )
    datastore_object_storage_layout: bool = Field(
        default=False,
        description=(
            "Write file bytes under ``pods/<pod>/objects/<file id>/<sha256>/`` "
            "instead of under their path, and copy existing files across in the "
            "background. A file stored this way is renamed or moved without "
            "touching its bytes. Reads look in both places while the copy runs. "
            "Env: ``DATASTORE_OBJECT_STORAGE_LAYOUT``."
        ),
    )
    datastore_search_scope_cache_ttl_seconds: int = Field(
        default=120,
        description=(
            "How long a caller's searchable-file split is reused from Redis. Any "
            "grant, visibility, membership, create, delete, move or completed "
            "indexing in the pod invalidates it sooner. 0 disables the cache. "
            "Env: ``DATASTORE_SEARCH_SCOPE_CACHE_TTL_SECONDS``."
        ),
    )
    datastore_search_scope_cache_max_ids: int = Field(
        default=20_000,
        description=(
            "A searchable split with more file ids than this is not cached. Env: "
            "``DATASTORE_SEARCH_SCOPE_CACHE_MAX_IDS``."
        ),
    )
    kreuzberg_health_timeout_seconds: float = Field(
        default=5.0,
        description=(
            "Timeout for the Kreuzberg health check asked after a 5xx or timeout "
            "on one document: healthy means the document is at fault (an "
            "attempt is charged), unhealthy means the extractor is down (it is "
            "not). Env: ``KREUZBERG_HEALTH_TIMEOUT_SECONDS``."
        ),
    )
