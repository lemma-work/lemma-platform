"""Pod bundle module configuration."""

from lemma_pod_bundle.limits import (
    MAX_APP_BYTES,
    MAX_APPS_TOTAL_BYTES,
    MAX_DATA_TOTAL_BYTES,
    MAX_ITEM_BYTES,
    MAX_RECORDS_PER_TABLE,
    MAX_RECORDS_TOTAL,
)
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


class PodBundleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(), env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    pod_bundle_state_ttl_seconds: int = Field(
        default=6 * 60 * 60,
        description=(
            "TTL for ephemeral import/export/publish job state in Redis, "
            "refreshed on every write. Staged bundle archives in object storage "
            "are swept on the same horizon. Expired state is never an error to "
            "recover from — re-running plans a fresh diff against the pod."
        ),
    )
    pod_bundle_max_archive_bytes: int = Field(
        default=100 * 1024 * 1024,
        description="Maximum accepted size (bytes) of an uploaded/fetched bundle archive.",
    )
    pod_bundle_max_uncompressed_bytes: int = Field(
        default=500 * 1024 * 1024,
        description=(
            "Ceiling on the total uncompressed size of a bundle archive "
            "(zip-bomb guard applied during extraction)."
        ),
    )
    pod_bundle_staging_prefix: str = Field(
        default="pod-bundle-staging",
        description=(
            "Key prefix (and local-backend subdirectory) under which staged "
            "bundle archives live in object storage."
        ),
    )
    pod_bundle_github_api_base: str = Field(
        default="https://api.github.com",
        description=(
            "Base URL for the GitHub REST API used to fetch a public repo's "
            "zipball on import. Overridable (POD_BUNDLE_GITHUB_API_BASE) so tests "
            "can point it at a local fixture server."
        ),
    )
    pod_bundle_github_fetch_timeout_seconds: float = Field(
        default=30.0,
        description="HTTP timeout (seconds) for fetching a GitHub repo zipball.",
    )

    # --- Per-user daily abuse guard --------------------------------------
    # Export and import are long-running jobs (archive assembly, agentbox app
    # builds, multi-resource apply). A generous per-user daily cap stops a
    # single account from hammering the workers; buckets are independent so a
    # day of exports never blocks imports (and vice versa). 0 disables the cap.
    pod_bundle_daily_export_limit: int = Field(
        default=5,
        description=(
            "Max export jobs a single user may start per UTC day "
            "(env: POD_BUNDLE_DAILY_EXPORT_LIMIT). 0 disables the limit."
        ),
    )
    pod_bundle_daily_import_limit: int = Field(
        default=5,
        description=(
            "Max import jobs a single user may start per UTC day "
            "(env: POD_BUNDLE_DAILY_IMPORT_LIMIT). 0 disables the limit."
        ),
    )

    # --- Export download URL retention -----------------------------------
    pod_bundle_export_url_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        description=(
            "Default lifetime (seconds) of an export's signed download URL, and "
            "the retention horizon of the staged export archive + its Redis "
            "state. Longer than the ~6h import horizon so a shared export stays "
            "fetchable; re-export is cheap when it expires."
        ),
    )
    pod_bundle_export_url_max_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        description="Hard ceiling (seconds) on a caller-requested export URL TTL.",
    )

    # --- Export data/asset caps (never dump GBs) -------------------------
    # The schema always exports in full; only row data and file/asset BYTES are
    # bounded, best-effort with warnings surfaced on the export status.
    # Defaults live in lemma_pod_bundle.limits (shared with the CLI); kept
    # env-overridable here for ops.
    pod_bundle_export_max_records_per_table: int = Field(
        default=MAX_RECORDS_PER_TABLE,
        description="Max rows written to a table's data.csv seed (per table).",
    )
    pod_bundle_export_max_records_total: int = Field(
        default=MAX_RECORDS_TOTAL,
        description=(
            "Max rows across all tables in one export; once reached, remaining "
            "tables export schema-only (no data.csv). Kept low on purpose — a "
            "bundle ships seed/setup rows, not a full data dump, and a large "
            "export hammers the pod DB."
        ),
    )
    pod_bundle_export_max_file_bytes: int = Field(
        default=MAX_ITEM_BYTES,
        description=(
            "Per-item byte ceiling for one DATA payload — a table's data.csv or a "
            "single pod file. Anything larger is skipped/truncated with a warning."
        ),
    )
    pod_bundle_export_max_files_total_bytes: int = Field(
        default=MAX_DATA_TOTAL_BYTES,
        description=(
            "Shared byte budget for pod DATA in one export — table row data + pod "
            "files draw from this single pool (app builds are budgeted separately). "
            "Kept small on purpose: a bundle ships skills/scripts/small seed tables "
            "(UI defaults), not a data dump."
        ),
    )
    pod_bundle_export_max_app_bytes: int = Field(
        default=MAX_APP_BYTES,
        description=(
            "Per-app byte ceiling for a single app build (source or dist archive). "
            "An app larger than this exports metadata-only with a warning."
        ),
    )
    pod_bundle_export_max_apps_total_bytes: int = Field(
        default=MAX_APPS_TOTAL_BYTES,
        description=(
            "Byte budget for ALL app builds combined in one export — separate from "
            "the data/files pool. Once spent, remaining apps export metadata-only."
        ),
    )


pod_bundle_settings = PodBundleSettings()
