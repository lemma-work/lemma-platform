"""Golden test for datastore config: env-var names + defaults preserved."""

from __future__ import annotations

import pytest

from app.modules.datastore.config import DatastoreSettings

pytestmark = pytest.mark.unit

# (field, ENV var, default) transcribed from the former app/core/config.py.
EXPECTED = [
    ("datastore_query_role", "DATASTORE_QUERY_ROLE", "lemma_datastore_query"),
    ("datastore_query_statement_timeout_ms", "DATASTORE_QUERY_STATEMENT_TIMEOUT_MS", 5000),
    ("datastore_query_max_rows", "DATASTORE_QUERY_MAX_ROWS", 1000),
    ("datastore_query_max_cost", "DATASTORE_QUERY_MAX_COST", 1_000_000.0),
    ("datastore_query_max_plan_rows", "DATASTORE_QUERY_MAX_PLAN_ROWS", 5_000_000),
    ("document_processing_max_concurrency", "DOCUMENT_PROCESSING_MAX_CONCURRENCY", 2),
    ("document_processing_debounce_seconds", "DOCUMENT_PROCESSING_DEBOUNCE_SECONDS", 300),
    ("pdf_ocr_detection_sample_pages", "PDF_OCR_DETECTION_SAMPLE_PAGES", 5),
    ("pdf_ocr_detection_min_chars_per_page", "PDF_OCR_DETECTION_MIN_CHARS_PER_PAGE", 100),
    ("kreuzberg_url", "KREUZBERG_URL", "http://localhost:8002"),
    ("kreuzberg_request_timeout_seconds", "KREUZBERG_REQUEST_TIMEOUT_SECONDS", 180.0),
    ("kreuzberg_transient_retry_attempts", "KREUZBERG_TRANSIENT_RETRY_ATTEMPTS", 5),
    (
        "kreuzberg_transient_retry_base_delay_seconds",
        "KREUZBERG_TRANSIENT_RETRY_BASE_DELAY_SECONDS",
        0.5,
    ),
    ("pdf_render_dpi", "PDF_RENDER_DPI", 150),
    ("pdf_render_max_long_edge", "PDF_RENDER_MAX_LONG_EDGE", 1568),
    ("pdf_render_jpeg_quality", "PDF_RENDER_JPEG_QUALITY", 80),
    ("pdf_render_max_pages_per_call", "PDF_RENDER_MAX_PAGES_PER_CALL", 10),
    ("pdf_render_concurrency", "PDF_RENDER_CONCURRENCY", 2),
    ("datastore_file_url_expiry_seconds", "DATASTORE_FILE_URL_EXPIRY_SECONDS", 3600),
    ("datastore_signed_url_default_expiry_seconds", "DATASTORE_SIGNED_URL_DEFAULT_EXPIRY_SECONDS", 10800),
    ("datastore_signed_url_max_expiry_seconds", "DATASTORE_SIGNED_URL_MAX_EXPIRY_SECONDS", 86400),
    ("datastore_signed_url_default_max_hits", "DATASTORE_SIGNED_URL_DEFAULT_MAX_HITS", 50),
    ("datastore_signed_url_max_hits", "DATASTORE_SIGNED_URL_MAX_HITS", 100),
    ("datastore_signed_url_code_bytes", "DATASTORE_SIGNED_URL_CODE_BYTES", 9),
]


def _clear(monkeypatch):
    for _, env, _default in EXPECTED:
        monkeypatch.delenv(env, raising=False)


def test_datastore_settings_defaults():
    # Declared defaults only — immune to a developer's local .env / os.environ.
    for field, _env, default in EXPECTED:
        assert DatastoreSettings.model_fields[field].default == default, field


def test_datastore_settings_field_set_is_exact():
    assert set(DatastoreSettings.model_fields) == {f for f, _e, _d in EXPECTED}


@pytest.mark.parametrize("field,env,_default", EXPECTED)
def test_datastore_settings_reads_legacy_env_var(monkeypatch, field, env, _default):
    _clear(monkeypatch)
    monkeypatch.setenv(env, "7")
    value = getattr(DatastoreSettings(), field)
    assert str(value).startswith("7")
