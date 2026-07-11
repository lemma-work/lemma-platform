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
    ("datastore_query_role", "DATASTORE_QUERY_ROLE", "lemma_datastore_query"),
    (
        "datastore_query_statement_timeout_ms",
        "DATASTORE_QUERY_STATEMENT_TIMEOUT_MS",
        5000,
    ),
    ("datastore_query_max_rows", "DATASTORE_QUERY_MAX_ROWS", 1000),
    ("datastore_query_max_cost", "DATASTORE_QUERY_MAX_COST", 1_000_000.0),
    ("datastore_query_max_plan_rows", "DATASTORE_QUERY_MAX_PLAN_ROWS", 5_000_000),
    ("document_processing_max_concurrency", "DOCUMENT_PROCESSING_MAX_CONCURRENCY", 2),
    (
        "document_processing_debounce_seconds",
        "DOCUMENT_PROCESSING_DEBOUNCE_SECONDS",
        300,
    ),
    ("recovery_enqueue_batch_size", "RECOVERY_ENQUEUE_BATCH_SIZE", 10),
    ("datastore_recovery_max_attempts", "DATASTORE_RECOVERY_MAX_ATTEMPTS", 3),
    (
        "document_processing_max_file_bytes",
        "DOCUMENT_PROCESSING_MAX_FILE_BYTES",
        104_857_600,
    ),
    (
        "document_processing_max_inflight_bytes",
        "DOCUMENT_PROCESSING_MAX_INFLIGHT_BYTES",
        0,
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
    ("pdf_render_dpi", "PDF_RENDER_DPI", 150),
    ("pdf_render_max_long_edge", "PDF_RENDER_MAX_LONG_EDGE", 1568),
    ("pdf_render_jpeg_quality", "PDF_RENDER_JPEG_QUALITY", 80),
    ("pdf_render_max_pages_per_call", "PDF_RENDER_MAX_PAGES_PER_CALL", 10),
    ("pdf_render_concurrency", "PDF_RENDER_CONCURRENCY", 2),
    ("datastore_file_url_expiry_seconds", "DATASTORE_FILE_URL_EXPIRY_SECONDS", 3600),
    (
        "datastore_signed_url_default_expiry_seconds",
        "DATASTORE_SIGNED_URL_DEFAULT_EXPIRY_SECONDS",
        10800,
    ),
    (
        "datastore_signed_url_max_expiry_seconds",
        "DATASTORE_SIGNED_URL_MAX_EXPIRY_SECONDS",
        86400,
    ),
    (
        "datastore_signed_url_default_max_hits",
        "DATASTORE_SIGNED_URL_DEFAULT_MAX_HITS",
        50,
    ),
    ("datastore_signed_url_max_hits", "DATASTORE_SIGNED_URL_MAX_HITS", 100),
    ("datastore_signed_url_code_bytes", "DATASTORE_SIGNED_URL_CODE_BYTES", 9),
]

# Non-numeric fields (bool / Literal) that the "env=7" parametrization below
# cannot exercise — validated with dedicated tests instead.
EXTRA_FIELDS = [
    ("document_processing_ocr_enabled", "DOCUMENT_PROCESSING_OCR_ENABLED", False),
    ("document_processing_layout_enabled", "DOCUMENT_PROCESSING_LAYOUT_ENABLED", True),
    ("document_processor", "DOCUMENT_PROCESSOR", "auto"),
]


def _clear(monkeypatch):
    for _, env, _default in EXPECTED:
        monkeypatch.delenv(env, raising=False)


def test_datastore_settings_defaults():
    # Declared defaults only — immune to a developer's local .env / os.environ.
    for field, _env, default in [*EXPECTED, *EXTRA_FIELDS]:
        assert DatastoreSettings.model_fields[field].default == default, field


def test_datastore_settings_field_set_is_exact():
    assert set(DatastoreSettings.model_fields) == {
        f for f, _e, _d in [*EXPECTED, *EXTRA_FIELDS]
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
    assert without_url.effective_document_processor() == "markitdown"


def test_effective_document_processor_explicit_wins(monkeypatch):
    monkeypatch.setenv("DOCUMENT_PROCESSOR", "markitdown")
    # Explicit choice is honoured even though a Kreuzberg URL is present.
    assert DatastoreSettings().effective_document_processor() == "markitdown"
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
    assert settings.effective_document_processor() == "markitdown"
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
