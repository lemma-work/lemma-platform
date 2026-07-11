"""Prevent module-owned settings from drifting back into global app config."""

from app.core.config import Settings


def test_global_settings_exclude_module_owned_controls() -> None:
    module_owned = {
        "agent_run_stop_poll_interval_seconds",
        "agent_context_brief_cache_ttl_seconds",
        "function_run_poll_interval_seconds",
        "conversation_title_model",
        "daemon_ws_ping_stale_after_seconds",
        "daemon_reconnect_grace_seconds",
        "local_agent_runtime_config_path",
        "icon_upload_max_bytes",
        "icon_max_dimension_pixels",
        "icon_max_total_pixels",
        "datastore_upload_max_bytes",
        "datastore_markdown_max_bytes",
        "datastore_markdown_image_max_bytes",
        "datastore_markdown_batch_max_bytes",
        "app_source_archive_max_bytes",
        "app_dist_archive_max_bytes",
        "app_bundle_upload_max_bytes",
        "app_archive_max_entries",
        "app_archive_max_uncompressed_bytes",
        "app_archive_max_compression_ratio",
        "scheduler_api_url",
        "schedule_max_consecutive_failures",
        "scheduler_internal_token",
    }

    assert module_owned.isdisjoint(Settings.model_fields)
    assert {"database_url", "redis_url", "max_request_body_bytes"} <= set(
        Settings.model_fields
    )
