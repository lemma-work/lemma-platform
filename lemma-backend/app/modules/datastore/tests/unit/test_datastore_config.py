"""Golden test for datastore config: env-var names + defaults preserved."""

from __future__ import annotations

import pytest

from app.modules.datastore.config import DatastoreSettings

pytestmark = pytest.mark.unit

# (field, ENV var, default) transcribed from the former app/core/config.py.
EXPECTED = [
    ("datastore_upload_max_bytes", "DATASTORE_UPLOAD_MAX_BYTES", 100 * 1024 * 1024),
    ("datastore_markdown_max_bytes", "DATASTORE_MARKDOWN_MAX_BYTES", 25 * 1024 * 1024),
    (
        "datastore_markdown_image_max_bytes",
        "DATASTORE_MARKDOWN_IMAGE_MAX_BYTES",
        10 * 1024 * 1024,
    ),
    (
        "datastore_markdown_batch_max_bytes",
        "DATASTORE_MARKDOWN_BATCH_MAX_BYTES",
        50 * 1024 * 1024,
    ),
    ("datastore_cell_max_bytes", "DATASTORE_CELL_MAX_BYTES", 256 * 1024),
    ("datastore_row_max_bytes", "DATASTORE_ROW_MAX_BYTES", 1024 * 1024),
    ("datastore_query_role", "DATASTORE_QUERY_ROLE", "lemma_datastore_query"),
    (
        "datastore_query_statement_timeout_ms",
        "DATASTORE_QUERY_STATEMENT_TIMEOUT_MS",
        5000,
    ),
    ("datastore_query_max_rows", "DATASTORE_QUERY_MAX_ROWS", 1000),
    ("datastore_query_max_cost", "DATASTORE_QUERY_MAX_COST", 1_000_000.0),
    ("datastore_query_max_plan_rows", "DATASTORE_QUERY_MAX_PLAN_ROWS", 5_000_000),
    (
        "datastore_search_readable_id_pushdown_limit",
        "DATASTORE_SEARCH_READABLE_ID_PUSHDOWN_LIMIT",
        5_000,
    ),
    (
        "datastore_search_scope_timeout_ms",
        "DATASTORE_SEARCH_SCOPE_TIMEOUT_MS",
        3_000,
    ),
    ("document_processing_max_concurrency", "DOCUMENT_PROCESSING_MAX_CONCURRENCY", 2),
    # Seconds, not minutes: until a replaced document's new revision is
    # indexed, search still answers with the old one (factory QA AC-PR979-3).
    (
        "document_processing_debounce_seconds",
        "DOCUMENT_PROCESSING_DEBOUNCE_SECONDS",
        10,
    ),
    (
        "document_processing_extractor_max_threads",
        "DOCUMENT_PROCESSING_EXTRACTOR_MAX_THREADS",
        4,
    ),
    ("document_processing_max_attempts", "DOCUMENT_PROCESSING_MAX_ATTEMPTS", 3),
    (
        "document_processing_unavailable_budget_seconds",
        "DOCUMENT_PROCESSING_UNAVAILABLE_BUDGET_SECONDS",
        86_400,
    ),
    ("document_processing_max_pages", "DOCUMENT_PROCESSING_MAX_PAGES", 2_000),
    ("document_processing_max_ocr_pages", "DOCUMENT_PROCESSING_MAX_OCR_PAGES", 500),
    ("document_processing_max_chunks", "DOCUMENT_PROCESSING_MAX_CHUNKS", 20_000),
    (
        "document_processing_retry_cooldown_seconds",
        "DOCUMENT_PROCESSING_RETRY_COOLDOWN_SECONDS",
        60,
    ),
    (
        "datastore_search_scope_cache_ttl_seconds",
        "DATASTORE_SEARCH_SCOPE_CACHE_TTL_SECONDS",
        120,
    ),
    (
        "datastore_search_scope_cache_max_ids",
        "DATASTORE_SEARCH_SCOPE_CACHE_MAX_IDS",
        20_000,
    ),
    ("kreuzberg_health_timeout_seconds", "KREUZBERG_HEALTH_TIMEOUT_SECONDS", 5.0),
    (
        "document_processing_max_file_bytes",
        "DOCUMENT_PROCESSING_MAX_FILE_BYTES",
        104_857_600,
    ),
    (
        "document_processing_max_inflight_bytes",
        "DOCUMENT_PROCESSING_MAX_INFLIGHT_BYTES",
        536_870_912,
    ),
    ("pdf_ocr_detection_sample_pages", "PDF_OCR_DETECTION_SAMPLE_PAGES", 5),
    (
        "pdf_ocr_detection_min_chars_per_page",
        "PDF_OCR_DETECTION_MIN_CHARS_PER_PAGE",
        100,
    ),
    ("docling_serve_url", "DOCLING_SERVE_URL", None),
    ("docling_request_timeout_seconds", "DOCLING_REQUEST_TIMEOUT_SECONDS", 300.0),
    ("kreuzberg_url", "KREUZBERG_URL", "http://localhost:8002"),
    ("kreuzberg_request_timeout_seconds", "KREUZBERG_REQUEST_TIMEOUT_SECONDS", 600.0),
    ("kreuzberg_connect_timeout_seconds", "KREUZBERG_CONNECT_TIMEOUT_SECONDS", 8.0),
    ("kreuzberg_transient_retry_attempts", "KREUZBERG_TRANSIENT_RETRY_ATTEMPTS", 3),
    (
        "kreuzberg_transient_retry_base_delay_seconds",
        "KREUZBERG_TRANSIENT_RETRY_BASE_DELAY_SECONDS",
        1.0,
    ),
    ("kreuzberg_circuit_failure_threshold", "KREUZBERG_CIRCUIT_FAILURE_THRESHOLD", 5),
    ("kreuzberg_circuit_reset_seconds", "KREUZBERG_CIRCUIT_RESET_SECONDS", 30.0),
    (
        "kreuzberg_max_response_bytes",
        "KREUZBERG_MAX_RESPONSE_BYTES",
        256 * 1024 * 1024,
    ),
    ("pdf_render_dpi", "PDF_RENDER_DPI", 150),
    ("pdf_render_max_long_edge", "PDF_RENDER_MAX_LONG_EDGE", 1568),
    ("pdf_render_jpeg_quality", "PDF_RENDER_JPEG_QUALITY", 80),
    ("pdf_render_max_pages_per_call", "PDF_RENDER_MAX_PAGES_PER_CALL", 10),
    ("pdf_render_concurrency", "PDF_RENDER_CONCURRENCY", 2),
    ("datastore_file_url_expiry_seconds", "DATASTORE_FILE_URL_EXPIRY_SECONDS", 3600),
    (
        "datastore_signed_url_default_expiry_seconds",
        "DATASTORE_SIGNED_URL_DEFAULT_EXPIRY_SECONDS",
        86400,
    ),
    (
        "datastore_signed_url_max_expiry_seconds",
        "DATASTORE_SIGNED_URL_MAX_EXPIRY_SECONDS",
        604800,
    ),
    (
        "datastore_signed_url_default_max_hits",
        "DATASTORE_SIGNED_URL_DEFAULT_MAX_HITS",
        200,
    ),
    ("datastore_signed_url_max_hits", "DATASTORE_SIGNED_URL_MAX_HITS", 1000),
    (
        "datastore_signed_url_max_active_per_user",
        "DATASTORE_SIGNED_URL_MAX_ACTIVE_PER_USER",
        500,
    ),
    (
        "datastore_signed_url_row_retention_seconds",
        "DATASTORE_SIGNED_URL_ROW_RETENTION_SECONDS",
        604800,
    ),
    ("datastore_signed_url_code_bytes", "DATASTORE_SIGNED_URL_CODE_BYTES", 9),
    (
        "datastore_orphan_schema_retention_days",
        "DATASTORE_ORPHAN_SCHEMA_RETENTION_DAYS",
        30,
    ),
]

# Non-numeric fields (bool / Literal) that the "env=7" parametrization below
# cannot exercise — validated with dedicated tests instead.
EXTRA_FIELDS = [
    ("datastore_change_retention_seconds", "DATASTORE_CHANGE_RETENTION_SECONDS", 3600),
    ("datastore_change_max_lag_seconds", "DATASTORE_CHANGE_MAX_LAG_SECONDS", 86400),
    (
        "datastore_change_handoff_poll_seconds",
        "DATASTORE_CHANGE_HANDOFF_POLL_SECONDS",
        1.0,
    ),
    ("document_processing_ocr_enabled", "DOCUMENT_PROCESSING_OCR_ENABLED", False),
    ("document_processing_layout_enabled", "DOCUMENT_PROCESSING_LAYOUT_ENABLED", True),
    ("document_processor", "DOCUMENT_PROCESSOR", "auto"),
    (
        "document_processing_layout_strategy",
        "DOCUMENT_PROCESSING_LAYOUT_STRATEGY",
        "auto",
    ),
    ("document_processing_table_model", "DOCUMENT_PROCESSING_TABLE_MODEL", "tatr"),
    (
        "datastore_object_storage_layout",
        "DATASTORE_OBJECT_STORAGE_LAYOUT",
        False,
    ),
]


def _clear(monkeypatch):
    for _, env, _default in EXPECTED:
        monkeypatch.delenv(env, raising=False)


# Moved out of `app/core/config.py`. Listed here for the same reason as
# everything above: the field set is exact, so a field arriving or leaving
# has to be a decision. Defaults transcribed from core before the move and
# checked against it -- all seventeen came across unchanged.
MOVED_FROM_CORE = [
    (
        "datastore_database_url",
        "DATASTORE_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/lemma_datastore",
    ),
    ("local_embedding_preload", "LOCAL_EMBEDDING_PRELOAD", True),
    (
        "local_embedding_preload_timeout_seconds",
        "LOCAL_EMBEDDING_PRELOAD_TIMEOUT_SECONDS",
        900.0,
    ),
    ("local_embedding_startup_mode", "LOCAL_EMBEDDING_STARTUP_MODE", "blocking"),
    (
        "openai_compat_reranker_model",
        "OPENAI_COMPAT_RERANKER_MODEL",
        "qwen3-reranker-8b",
    ),
    ("reranker_mode", "RERANKER_MODE", "off"),
    ("reranker_retrieve_n", "RERANKER_RETRIEVE_N", 50),
    ("local_reranker_model", "LOCAL_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
    # The 0.8.0 config audit: the last core field only this module read.
    (
        "e2e_disable_worker_file_autoindex",
        "E2E_DISABLE_WORKER_FILE_AUTOINDEX",
        False,
    ),
]


def test_datastore_settings_defaults():
    # Declared defaults only — immune to a developer's local .env / os.environ.
    for field, _env, default in [*EXPECTED, *EXTRA_FIELDS, *MOVED_FROM_CORE]:
        assert DatastoreSettings.model_fields[field].default == default, field


def test_datastore_settings_field_set_is_exact():
    assert set(DatastoreSettings.model_fields) == {
        f for f, _e, _d in [*EXPECTED, *EXTRA_FIELDS, *MOVED_FROM_CORE]
    }


def test_document_processing_ocr_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("DOCUMENT_PROCESSING_OCR_ENABLED", "true")
    assert DatastoreSettings().document_processing_ocr_enabled is True
    monkeypatch.setenv("DOCUMENT_PROCESSING_OCR_ENABLED", "false")
    assert DatastoreSettings().document_processing_ocr_enabled is False


def test_effective_document_processor_auto_follows_kreuzberg_url(monkeypatch):
    monkeypatch.delenv("DOCUMENT_PROCESSOR", raising=False)
    with_url = DatastoreSettings(kreuzberg_url="http://kreuzberg:8000")
    assert with_url.effective_document_processor() == "kreuzberg"
    without_url = DatastoreSettings(kreuzberg_url="")
    assert without_url.effective_document_processor() == "xberg"


def test_effective_document_processor_explicit_wins(monkeypatch):
    monkeypatch.setenv("DOCUMENT_PROCESSOR", "xberg")
    # Explicit choice is honoured even though a Kreuzberg URL is present.
    assert DatastoreSettings().effective_document_processor() == "xberg"
    monkeypatch.setenv("DOCUMENT_PROCESSOR", "kreuzberg")
    assert (
        DatastoreSettings(kreuzberg_url="").effective_document_processor()
        == "kreuzberg"
    )


def test_effective_document_processor_auto_never_selects_docling(monkeypatch):
    monkeypatch.delenv("DOCUMENT_PROCESSOR", raising=False)
    # Docling is opt-in only: auto ignores it even when DOCLING_SERVE_URL is set.
    settings = DatastoreSettings(
        kreuzberg_url="", docling_serve_url="http://docling:5001"
    )
    assert settings.effective_document_processor() == "xberg"
    # ...but an explicit choice activates it.
    assert (
        DatastoreSettings(document_processor="docling").effective_document_processor()
        == "docling"
    )


def test_document_processing_layout_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("DOCUMENT_PROCESSING_LAYOUT_ENABLED", raising=False)
    assert DatastoreSettings().document_processing_layout_enabled is True
    monkeypatch.setenv("DOCUMENT_PROCESSING_LAYOUT_ENABLED", "false")
    assert DatastoreSettings().document_processing_layout_enabled is False


@pytest.mark.parametrize("field,env,_default", EXPECTED)
def test_datastore_settings_reads_legacy_env_var(monkeypatch, field, env, _default):
    _clear(monkeypatch)
    monkeypatch.setenv(env, "7")
    value = getattr(DatastoreSettings(), field)
    assert str(value).startswith("7")
