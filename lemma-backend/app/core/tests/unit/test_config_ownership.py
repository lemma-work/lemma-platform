"""Prevent module-owned settings from drifting back into global app config."""

from app.core.config import Settings


def test_global_settings_exclude_module_owned_controls() -> None:
    module_owned = {
        "agent_run_stop_poll_interval_seconds",
        "agent_context_brief_cache_ttl_seconds",
        "agent_memory_index_max_chars",
        "agent_memory_section_max_chars",
        "agent_memory_brief_cache_ttl_seconds",
        "function_run_poll_interval_seconds",
        # The thirteen that moved to `app/modules/function/config.py`. Every one
        # is read only inside `mod:function`, which is what made this the
        # cleanest cluster to move first. No `AliasChoices` was needed: no
        # settings class sets `env_prefix`, so pydantic-settings derives the env
        # var from the field name identically on both classes.
        "function_api_deadline_seconds",
        "function_builder_digest",
        "function_builder_executable",
        "function_builder_python_platform",
        "function_job_deadline_seconds",
        "function_run_retention_batch_size",
        "function_run_retention_budget_seconds",
        "function_run_retention_days",
        "function_runtime_endpoint_cache_max_entries",
        "function_runtime_endpoint_reuse_seconds",
        "function_runtime_gateway_url",
        "function_session_token_cache_max_entries",
        "function_session_token_cache_ttl_seconds",
        # Moved to `app/modules/datastore/config.py`.
        "datastore_database_url",
        "local_embedding_preload",
        "local_embedding_preload_timeout_seconds",
        "local_embedding_startup_mode",
        "local_reranker_model",
        "openai_compat_reranker_model",
        "reranker_mode",
        "reranker_retrieve_n",
        # Moved to `app/modules/agent/config.py`. `lemma_openai_api_key` and
        # `lemma_openai_base_url` are deliberately absent: core embeddings,
        # datastore's reranker and a catalog script read them too.
        "lemma_anthropic_api_key",
        "lemma_anthropic_base_url",
        "lemma_anthropic_default_model",
        "lemma_anthropic_model_names",
        "lemma_default_model_type",
        "lemma_llm_caching_enabled",
        "lemma_openai_default_model",
        "lemma_openai_model_names",
        "lemma_openai_vision_model_names",
        "conversation_title_model",
        "local_agent_runtime_config_path",
        "icon_upload_max_bytes",
        "icon_max_dimension_pixels",
        "icon_max_total_pixels",
        "datastore_upload_max_bytes",
        "datastore_markdown_max_bytes",
        "datastore_markdown_image_max_bytes",
        "datastore_markdown_batch_max_bytes",
        "datastore_cell_max_bytes",
        "datastore_row_max_bytes",
        "datastore_event_payload_max_bytes",
        "app_source_archive_max_bytes",
        "app_dist_archive_max_bytes",
        "app_bundle_upload_max_bytes",
        "app_archive_max_entries",
        "app_archive_max_uncompressed_bytes",
        "app_archive_max_compression_ratio",
        "schedule_max_consecutive_failures",
    }

    assert module_owned.isdisjoint(Settings.model_fields)
    assert {"database_url", "redis_url", "max_request_body_bytes"} <= set(
        Settings.model_fields
    )
