"""Datastore module configuration.

Field names are unchanged from the former monolithic ``Settings`` so the
environment variables resolve identically (``DATASTORE_QUERY_MAX_ROWS``,
``PDF_RENDER_DPI``, ``KREUZBERG_URL``, …).

NOTE: ``datastore_database_url`` deliberately stays in core ``Settings`` — it is
a second database URL (infrastructure, parallel to ``database_url``) and the e2e
test infra mutates it on the shared settings object. Embedding settings also stay
in core (consumed by ``app/core/embeddings``).
"""

from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


class DatastoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # API upload limits
    datastore_upload_max_bytes: int = Field(default=100 * 1024 * 1024)
    datastore_markdown_max_bytes: int = Field(default=25 * 1024 * 1024)
    datastore_markdown_image_max_bytes: int = Field(default=10 * 1024 * 1024)
    datastore_markdown_batch_max_bytes: int = Field(default=50 * 1024 * 1024)

    # Record value limits. Tables hold tabular data; a document belongs in a pod
    # file with its path in a FILE_PATH column. Until these existed, every byte
    # arriving as a file passed a ceiling and every byte arriving as a record
    # cell passed none -- which is how a multi-megabyte column filled Redis (the
    # whole row is copied into `datastore.events`, capped by entry count rather
    # than bytes) and stalled the API event loop decoding it. Production rows
    # have averaged ~2KB, so 256KB is roughly 100x real use and still makes a
    # megabyte document impossible. 0 disables the bound.
    datastore_cell_max_bytes: int = Field(
        default=256 * 1024,
        description=(
            "Largest encoded size (bytes) of a single record value. Exceeding it "
            "refuses the write. Env: ``DATASTORE_CELL_MAX_BYTES``."
        ),
    )
    datastore_row_max_bytes: int = Field(
        default=1024 * 1024,
        description=(
            "Largest encoded size (bytes) of one record across all its columns, "
            "so a row of just-legal cells is still bounded. Env: "
            "``DATASTORE_ROW_MAX_BYTES``."
        ),
    )
    datastore_event_payload_max_bytes: int = Field(
        default=32 * 1024,
        description=(
            "Largest encoded size (bytes) of the row body carried on a record "
            "change event. Above it the body is dropped and the event is flagged "
            "truncated; consumers read the row instead. Deliberately far below "
            "the row limit: a row is stored once, while its event is retained "
            "50,000 times over in a Redis stream capped by entry count. Env: "
            "``DATASTORE_EVENT_PAYLOAD_MAX_BYTES``."
        ),
    )

    # Ad-hoc SQL query guardrails
    datastore_query_role: str = Field(
        default="lemma_datastore_query",
        description=(
            "Non-superuser, NOBYPASSRLS database role that ad-hoc datastore SQL "
            "queries run under (via SET LOCAL ROLE) so row-level security is "
            "enforced. Must be a plain SQL identifier."
        ),
    )
    datastore_query_statement_timeout_ms: int = Field(
        default=5000,
        description="Per-statement timeout (ms) applied to ad-hoc datastore SQL queries (query.execute).",
    )
    datastore_query_max_rows: int = Field(
        default=1000,
        description="Maximum rows returned by an ad-hoc datastore SQL query; extra rows are truncated.",
    )
    datastore_query_max_cost: float = Field(
        default=1_000_000.0,
        description="Reject ad-hoc datastore SQL queries whose EXPLAIN total cost exceeds this ceiling.",
    )
    datastore_query_max_plan_rows: int = Field(
        default=5_000_000,
        description="Reject ad-hoc datastore SQL queries whose EXPLAIN estimated row count exceeds this ceiling.",
    )
    datastore_search_visibility_id_soft_limit: int = Field(
        default=20_000,
        description=(
            "Log a degraded event when a search's visibility filter has to send "
            "more than this many file ids to the pod database. Deliberately a "
            "warning threshold and not a cap: truncating the visible list would "
            "drop results, and truncating the hidden list would leak files the "
            "caller may not read, so neither side is ever trimmed."
        ),
    )

    # Document processing
    document_processing_max_concurrency: int = Field(
        default=2,
        description=(
            "Maximum concurrent document extractions per worker process. This is "
            "the primary lever on a worker's peak RAM during ingestion: each "
            "extraction holds the source document plus the extractor's response "
            "(markdown + chunks + any extracted images) in memory at once, so peak "
            "memory scales with this multiplier. Keep it low enough that "
            "concurrency times the largest expected per-document working set stays "
            "within the worker's memory budget; tune per deployment. Env: "
            "``DOCUMENT_PROCESSING_MAX_CONCURRENCY``."
        ),
    )
    document_processing_debounce_seconds: int = Field(
        default=300,
        description="Debounce window for datastore file content updates before enqueueing document processing.",
    )
    datastore_per_pod_max_inflight: int = Field(
        default=4,
        description=(
            "Maximum files one pod may have queued or mid-flight (PENDING or "
            "PROCESSING) before uploads stop being enqueued immediately. Beyond "
            "this the row is simply left PENDING and the fair dispatcher picks "
            "it up on its next tick, so a tenant bulk-uploading a thousand "
            "documents cannot monopolise ingestion or push every other tenant "
            "behind them. Nothing is dropped — the PENDING row is the durable "
            "backlog. 0 disables the gate (immediate enqueue for everything, "
            "the pre-fairness behaviour). Env: "
            "``DATASTORE_PER_POD_MAX_INFLIGHT``."
        ),
    )
    datastore_dispatch_global_batch: int = Field(
        default=50,
        description=(
            "Maximum files the fair dispatcher enqueues per tick across all "
            "pods. Bounds how much of the backlog is resident in Redis at once; "
            "the rest stays PENDING in Postgres and is dispatched on later "
            "ticks. Env: ``DATASTORE_DISPATCH_GLOBAL_BATCH``."
        ),
    )
    recovery_enqueue_batch_size: int = Field(
        default=10,
        description=(
            "The recovery cron re-drives stale files in batches of this size, "
            "yielding to the event loop between batches, so a large backlog is "
            "spread out instead of dispatched as one burst (which would spike "
            "worker task pickup and DB connection demand). Env: "
            "``RECOVERY_ENQUEUE_BATCH_SIZE``."
        ),
    )
    datastore_recovery_max_attempts: int = Field(
        default=3,
        description=(
            "Maximum times the recovery cron will (re)drive a file through "
            "processing before terminally failing it (status -> FAILED_PERMANENT). "
            "processing_attempts is incremented on each claim; once it reaches this "
            "cap the file is no longer re-driven, which stops a permanently-failing "
            "file (e.g. a down extractor, an unprocessable document) from looping "
            "forever and pinning worker memory/slots. A fresh upload / content "
            "update resets the counter. Env: ``DATASTORE_RECOVERY_MAX_ATTEMPTS``."
        ),
    )
    document_processing_max_file_bytes: int = Field(
        default=104_857_600,  # 100 MB
        description=(
            "Files larger than this are not processed: the whole file is buffered "
            "in memory during extraction, so an oversized file risks OOMing the "
            "worker. Such files are marked FAILED_PERMANENT with a clear reason "
            "rather than attempted. 0 disables the guard. Env: "
            "``DOCUMENT_PROCESSING_MAX_FILE_BYTES``."
        ),
    )
    document_processing_max_inflight_bytes: int = Field(
        default=536_870_912,  # 512 MB
        description=(
            "Aggregate cap (bytes) on document content held in memory across all "
            "concurrent extractions. Complements the bulk lane's concurrency (a "
            "count) by bounding total bytes, so a few large files cannot stack "
            "into an OOM — which is not hypothetical: the ingestion worker was "
            "OOM-killed at its 4GB limit during the 100-paper benchmark while "
            "this defaulted to 0. Enabled by default now; 0 disables the byte "
            "gate and leaves only the count. Env: "
            "``DOCUMENT_PROCESSING_MAX_INFLIGHT_BYTES``."
        ),
    )
    pdf_ocr_detection_sample_pages: int = Field(
        default=5,
        description=(
            "How many pages to sample (spread across the document) when probing a "
            "PDF with pypdfium2 to decide scanned-vs-native before extraction."
        ),
    )
    pdf_ocr_detection_min_chars_per_page: int = Field(
        default=100,
        description=(
            "If a sampled PDF averages fewer than this many extracted text "
            "characters per page it is treated as scanned (force OCR, 300-DPI "
            "images); otherwise native (no forced OCR, 150-DPI images). The "
            "layout/table config is applied to both so every doc gets rich "
            "markdown — only force_ocr and image DPI differ. Only consulted when "
            "``document_processing_ocr_enabled`` is true."
        ),
    )
    document_processing_ocr_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in switch for the heavy scanned-PDF path (Kreuzberg only). When "
            "false (the default), every document is extracted with the fast "
            "digital-first config — layout + tables + 150-DPI images, no OCR — so "
            "processing stays ~10-20s and bounded in RAM: the up-front pypdfium "
            "scanned-vs-native probe AND the reactive forced-OCR retry are both "
            "skipped. When true, scanned PDFs are detected and OCR'd at 300 DPI "
            "(Tesseract), which is the real resource/latency spike. Scanned docs "
            "under the default degrade to their text layer; bring your own "
            "markdown for those, or flip this on. Env: "
            "``DOCUMENT_PROCESSING_OCR_ENABLED``."
        ),
    )
    document_processing_layout_enabled: bool = Field(
        default=True,
        description=(
            "Enable Kreuzberg PDF layout and table models. Keep enabled for "
            "production-quality digital PDFs; the switch exists for hermetic tests "
            "and emergency low-resource operation. Env: "
            "``DOCUMENT_PROCESSING_LAYOUT_ENABLED``."
        ),
    )

    document_processing_layout_strategy: Literal["auto", "always"] = Field(
        default="auto",
        description=(
            "Which pages the layout model runs on. 'auto' pre-screens each page "
            "with cheap geometry signals and runs the model only where it can "
            "help (multi-column, table-bearing, figure-heavy, rotated); 'always' "
            "renders and infers every page. Layout inference dominates "
            "extraction cost, so 'auto' is the single biggest CPU-per-document "
            "lever. Honoured by Xberg 1.x; ignored by Kreuzberg v4, which has no "
            "page-selection knob. Env: "
            "``DOCUMENT_PROCESSING_LAYOUT_STRATEGY``."
        ),
    )
    document_processing_table_model: Literal[
        "tatr",
        "slanet_plus",
        "slanet_wired",
        "slanet_wireless",
        "slanet_auto",
        "disabled",
    ] = Field(
        default="tatr",
        description=(
            "Table-structure recognition model used inside layout-detected table "
            "regions. 'tatr' (~30MB) is the default; 'slanet_plus' (~8MB) suits "
            "memory-constrained deployments; the slanet_wired/wireless/auto "
            "variants are far larger (365MB+). 'disabled' skips table structure "
            "entirely. Env: ``DOCUMENT_PROCESSING_TABLE_MODEL``."
        ),
    )
    document_processing_extractor_max_threads: int = Field(
        default=4,
        description=(
            "Cap on the extractor's internal thread pool, sent as "
            "concurrency.max_threads in the per-request config. Unset, the "
            "extractor sizes its pool from the host CPU count and ignores the "
            "container's CPU limit, so concurrent extractions oversubscribe the "
            "box and each one gets slower. 0 leaves it to the extractor. Env: "
            "``DOCUMENT_PROCESSING_EXTRACTOR_MAX_THREADS``."
        ),
    )

    # Document-processor adapter selection
    document_processor: Literal[
        "auto", "kreuzberg", "xberg", "markitdown", "docling"
    ] = Field(
        default="auto",
        description=(
            "Which document-processor adapter converts non-markdown files to "
            "markdown. 'xberg' runs IN-PROCESS (optional dep; MIT; a Rust core "
            "with no models and no Python dependencies; what local and desktop "
            "installs use, since they have no container to run). 'docling' calls "
            "a Docling Serve container OVER HTTP (MIT; beautiful research-paper/"
            "book markdown with tables; ML-heavy + GPU-oriented, so it runs as "
            "its own service and the backend stays lean — opt-in only, set this "
            "to 'docling' + DOCLING_SERVE_URL). 'kreuzberg' calls the Kreuzberg "
            "REST container. 'auto' (the default) uses 'kreuzberg' when "
            "KREUZBERG_URL is set, else the in-process 'xberg'; it never "
            "auto-selects docling. Env: ``DOCUMENT_PROCESSOR``."
        ),
    )

    # Docling Serve (over-HTTP document processor; MIT-licensed alternative to
    # Kreuzberg — runs as its own container, keeps the backend torch-free).
    docling_serve_url: Optional[str] = Field(
        default=None,
        description=(
            "Docling Serve base URL (e.g. http://localhost:5001) for the 'docling' "
            "document processor. Unset by default; set it (or DOCUMENT_PROCESSOR="
            "docling) to route conversion to a Docling Serve container."
        ),
    )
    docling_request_timeout_seconds: float = Field(
        default=300.0,
        description=(
            "HTTP timeout (seconds) for a Docling Serve /v1/convert/file request. "
            "Higher than Kreuzberg's since Docling's layout+table models are CPU-"
            "heavier per document."
        ),
    )

    # Kreuzberg
    kreuzberg_url: Optional[str] = Field(
        default="http://localhost:8002",
        description="Kreuzberg API URL for document processing",
    )
    kreuzberg_request_timeout_seconds: float = Field(
        default=600.0,
        description=(
            "Total HTTP timeout (seconds) for a Kreuzberg extract/chunk request. "
            "Includes cold layout/table-model initialization and long digital PDFs; "
            "a DOWN endpoint no longer waits this long "
            "— see kreuzberg_connect_timeout_seconds."
        ),
    )
    kreuzberg_connect_timeout_seconds: float = Field(
        default=8.0,
        description=(
            "Connection-establishment timeout (seconds) for Kreuzberg requests "
            "(aiohttp connect/sock_connect). A down/unreachable extractor fails "
            "within this window instead of hanging to the full request timeout, so "
            "a Kreuzberg outage costs seconds per attempt, not minutes. Env: "
            "``KREUZBERG_CONNECT_TIMEOUT_SECONDS``."
        ),
    )
    kreuzberg_transient_retry_attempts: int = Field(
        default=3,
        description=(
            "Attempts for transient (connection/timeout) Kreuzberg failures before "
            "giving up. With the fast connect timeout above, the exponential "
            "backoff (base*(2^(attempts-1)-1)) is now the dominant wait, so the "
            "default 3/1.0s rides out ~3s of blips without letting a persistently "
            "down extractor pin a file for minutes. The circuit breaker "
            "short-circuits repeated failures. Env: "
            "``KREUZBERG_TRANSIENT_RETRY_ATTEMPTS``."
        ),
    )
    kreuzberg_transient_retry_base_delay_seconds: float = Field(
        default=1.0,
        description=(
            "Base delay (seconds) for exponential backoff between transient "
            "Kreuzberg retries; total wait is base*(2^(attempts-1)-1). Env: "
            "``KREUZBERG_TRANSIENT_RETRY_BASE_DELAY_SECONDS``."
        ),
    )
    kreuzberg_circuit_failure_threshold: int = Field(
        default=5,
        description=(
            "Consecutive connection failures before the in-process Kreuzberg "
            "circuit breaker opens. While open, extractions short-circuit "
            "immediately (the file is marked FAILED and retried later by the "
            "recovery cron) instead of each one burning the full retry budget "
            "against a known-down extractor. Env: "
            "``KREUZBERG_CIRCUIT_FAILURE_THRESHOLD``."
        ),
    )
    kreuzberg_circuit_reset_seconds: float = Field(
        default=30.0,
        description=(
            "How long the Kreuzberg circuit stays open before allowing a single "
            "trial request (half-open). A success closes it; a failure re-opens "
            "it. Env: ``KREUZBERG_CIRCUIT_RESET_SECONDS``."
        ),
    )

    # PDF page rendering (on-demand, in-backend via pypdfium2 + Pillow)
    pdf_render_dpi: int = Field(
        default=150, description="DPI used when rasterizing PDF pages to images."
    )
    pdf_render_max_long_edge: int = Field(
        default=1568,
        description=(
            "Max long-edge in pixels for a rendered page image. ~1568px matches "
            "the resolution vision models consume, so larger renders are wasted."
        ),
    )
    pdf_render_jpeg_quality: int = Field(
        default=80, description="JPEG quality (1-100) for rendered/cached page images."
    )
    pdf_render_max_pages_per_call: int = Field(
        default=10,
        description="Max pages a single render request may produce, to bound payload + memory.",
    )
    pdf_render_concurrency: int = Field(
        default=2,
        description=(
            "Max concurrent in-process PDF rasterizations. PDF rendering is "
            "CPU/memory-heavy; this gate prevents bursts from stacking renders and "
            "exhausting memory."
        ),
    )

    # Signed datastore file URLs.
    # Tokens are signed by the unified app/core/crypto signer (HKDF off the
    # required SECRET_ENCRYPTION_KEY) — no per-feature secret is configured here.
    datastore_file_url_expiry_seconds: int = Field(
        default=3600,
        description="Default lifetime (seconds) of a signed datastore file URL.",
    )

    # Public (short) signed datastore URLs
    datastore_signed_url_default_expiry_seconds: int = Field(
        default=10800,
        description=(
            "Default lifetime (seconds) of a public, hit-capped datastore signed "
            "(short) URL. Used when a caller does not specify an expiry."
        ),
    )
    datastore_signed_url_max_expiry_seconds: int = Field(
        default=86400,
        description=(
            "Hard ceiling (seconds) on a public datastore signed URL's lifetime. "
            "Requests above this are clamped down. Defaults to 24 hours."
        ),
    )
    datastore_signed_url_default_max_hits: int = Field(
        default=50,
        description=(
            "Default maximum number of times a public datastore signed URL may be "
            "fetched before it is rejected. Bounds egress from link misuse."
        ),
    )
    datastore_signed_url_max_hits: int = Field(
        default=100,
        description=(
            "Hard ceiling on the per-link hit cap for public datastore signed URLs. "
            "Requests above this are clamped down."
        ),
    )
    datastore_signed_url_code_bytes: int = Field(
        default=9,
        description=(
            "Entropy (bytes) for a public datastore signed URL's short code; "
            "secrets.token_urlsafe(9) yields a 12-character code."
        ),
    )

    def effective_document_processor(self) -> str:
        """Resolve ``document_processor`` to a concrete adapter name.

        'auto' uses Kreuzberg when a Kreuzberg URL is configured, otherwise the
        in-process 'xberg' adapter — so a stack that drops the Kreuzberg
        container (KREUZBERG_URL="") still converts documents in-process.

        Extraction is CPU-bound, which is why cloud keeps it in a container of
        its own rather than in the API process; a local install has no container
        fleet, so in-process is the only option there.

        Docling is intentionally NOT auto-selected: it is GPU-oriented (slow on
        CPU) so it is opt-in only via an explicit ``DOCUMENT_PROCESSOR=docling``
        (plus ``DOCLING_SERVE_URL``).
        """
        if self.document_processor == "markitdown":
            # Accepted, and resolved to its replacement. markitdown was the
            # in-process adapter until xberg took over, and the value is written
            # into every desktop host pack, every local stack .env, and any
            # deployment that pinned it -- so rejecting it would turn an upgrade
            # into a backend that refuses to start over a name.
            return "xberg"
        if self.document_processor != "auto":
            return self.document_processor
        return "kreuzberg" if (self.kreuzberg_url or "").strip() else "xberg"


datastore_settings = DatastoreSettings()
