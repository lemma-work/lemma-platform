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
        # ── Moved in the 0.8.0 config audit ───────────────────────────────
        # Twenty-five read only by `mod:identity`, which made it the largest
        # single-owner cluster left in core. The two `is_*_configured`
        # predicates went with them: they read `self.<field>`, which
        # `check_settings_attrs.py` cannot see, so leaving either behind would
        # have been an AttributeError at runtime under a green gate.
        "auth_altcha_hmac_key",
        "auth_altcha_max_number",
        "auth_bounce_webhook_secret",
        "auth_disposable_email_allowlist",
        "auth_jwks_unknown_kid_cache_size",
        "auth_jwks_unknown_kid_ttl_seconds",
        "auth_trusted_proxy_ips",
        "auth_website_base_path",
        "auth_whatsapp_mobile_verification_enabled",
        "desktop_auth_create_limit",
        "desktop_auth_create_window_seconds",
        # Google joined Microsoft here; both are read by the same function.
        "google_client_id",
        "google_client_secret",
        "microsoft_client_id",
        "microsoft_client_secret",
        "microsoft_tenant_id",
        "organization_home_cache_ttl_seconds",
        "session_cookie_domain",
        "session_cookie_older_domain",
        "session_cookie_same_site",
        "session_cookie_secure",
        "supertokens_api_base_path",
        "supertokens_api_gateway_path",
        "telegram_oidc_client_id",
        "telegram_oidc_client_secret",
        "telegram_oidc_redirect_uri",
        "user_cache_ttl_seconds",
        # Telegram's own OIDC endpoints stopped being settings entirely -- see
        # `test_telegram_oidc_endpoints_are_constants_not_settings`. Listed here
        # too: they must not come back as fields on `Settings` either.
        "telegram_oidc_issuer",
        "telegram_oidc_authorization_endpoint",
        "telegram_oidc_token_endpoint",
        "telegram_oidc_jwks_uri",
        # Single-owner clusters that went to the module that reads them.
        "workflow_wait_retention_days",
        "workflow_wait_retention_batch_size",
        "workflow_wait_retention_budget_seconds",
        "usage_org_monthly_limit_usd",
        "usage_user_weekly_limit_usd",
        "usage_user_monthly_limit_usd",
        "workspace_callback_api_url",
        "workspace_callback_auth_url",
        "workspace_callback_frontend_url",
        "app_branding_enabled",
        "schedule_poll_interval_seconds",
        "e2e_disable_worker_file_autoindex",
        # Deleted rather than moved: nothing in the repo read either, in any
        # language, and no environment set them.
        "gcp_project_id",
        "gcp_location",
    }

    assert module_owned.isdisjoint(Settings.model_fields)
    assert {"database_url", "redis_url", "max_request_body_bytes"} <= set(
        Settings.model_fields
    )
    # Kept in core deliberately: `mod:agent_surfaces` reads it and
    # `agent_surfaces -> workflow` is a forbidden import, so module ownership
    # here would have cost a dependency the architecture gate refuses.
    assert "workflow_wait_max_age_seconds" in Settings.model_fields


def test_moved_settings_actually_arrived_on_their_new_class() -> None:
    """Absence from core is only half the claim.

    The check above says a field is not on `Settings`. It has never said the
    field is anywhere at all -- so deleting one by accident, or moving it to a
    class nothing instantiates, reads exactly like a successful move. Each
    cluster is asserted against the class that now owns it.
    """
    from app.modules.apps.config import AppsSettings
    from app.modules.datastore.config import DatastoreSettings
    from app.modules.identity.config import IdentitySettings
    from app.modules.schedule.config import ScheduleSettings
    from app.modules.usage.config import UsageSettings
    from app.modules.workflow.config import WorkflowSettings
    from app.modules.workspace.config import WorkspaceSettings

    arrived = {
        IdentitySettings: {
            "auth_altcha_hmac_key",
            "session_cookie_older_domain",
            "supertokens_api_gateway_path",
            "telegram_oidc_client_id",
            "google_client_id",
            "user_cache_ttl_seconds",
        },
        WorkflowSettings: {
            "workflow_wait_retention_days",
        },
        UsageSettings: {"usage_org_monthly_limit_usd"},
        WorkspaceSettings: {"workspace_callback_api_url"},
        AppsSettings: {"app_branding_enabled"},
        ScheduleSettings: {"schedule_poll_interval_seconds"},
        DatastoreSettings: {"e2e_disable_worker_file_autoindex"},
    }
    for cls, fields in arrived.items():
        missing = fields - set(cls.model_fields)
        assert not missing, f"{cls.__name__} is missing {sorted(missing)}"
