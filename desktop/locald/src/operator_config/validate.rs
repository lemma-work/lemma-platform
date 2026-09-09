//! Refusing a configuration before any of it is written.

use super::*;

pub(crate) fn validate_config(config: &OperatorConfig) -> io::Result<()> {
    validate_config_shape(config)?;
    if config.ai.protocol != "unconfigured"
        && (config.ai.default_model.is_empty()
            || !config.ai.models.contains(&config.ai.default_model))
    {
        return Err(invalid(
            "AI default model must be included in the model list",
        ));
    }
    if config
        .ai
        .vision_models
        .iter()
        .any(|model| !config.ai.models.contains(model))
    {
        return Err(invalid(
            "vision models must be a subset of configured models",
        ));
    }
    Ok(())
}

pub(crate) fn validate_config_shape(config: &OperatorConfig) -> io::Result<()> {
    if config.schema_version != CONFIG_SCHEMA_VERSION
        || config.install_id.len() != 32
        || !config
            .install_id
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(invalid(
            "unsupported operator configuration identity or schema",
        ));
    }
    validate_ai_shape(&config.ai)?;
    for (label, value) in [
        ("Google client ID", &config.integrations.google_client_id),
        (
            "Microsoft client ID",
            &config.integrations.microsoft_client_id,
        ),
        ("Teams app ID", &config.surfaces.teams_app_id),
        ("Teams tenant ID", &config.surfaces.teams_tenant_id),
        (
            "WhatsApp phone number ID",
            &config.surfaces.whatsapp_phone_number_id,
        ),
        ("WhatsApp WABA ID", &config.surfaces.whatsapp_waba_id),
        (
            "Resend inbound domain",
            &config.surfaces.resend_inbound_domain,
        ),
    ] {
        validate_text(label, value, 2048)?;
    }
    Ok(())
}

/// The AI half of [`validate_config_shape`], reusable on its own.
///
/// `discover_models` accepts a candidate profile that has never been saved, and
/// it must be held to exactly the same rules as one that is about to be — a
/// probe is still an outbound request built from user-supplied text.
pub(crate) fn validate_ai_shape(ai: &AiProfile) -> io::Result<()> {
    if !matches!(
        ai.protocol.as_str(),
        "unconfigured" | "openai_compat" | "anthropic_compat"
    ) {
        return Err(invalid("unsupported AI provider protocol"));
    }
    validate_text("AI base URL", &ai.base_url, 2048)?;
    validate_text("AI default model", &ai.default_model, 2048)?;
    for model in ai.models.iter().chain(&ai.vision_models) {
        validate_text("model name", model, 256)?;
    }
    if ai.protocol != "unconfigured" && !valid_http_url(&ai.base_url) {
        return Err(invalid(
            "AI base URL must use HTTP or HTTPS without embedded credentials",
        ));
    }
    Ok(())
}

pub(crate) fn validate_capability_requirements(
    config: &OperatorConfig,
    secrets: &BTreeMap<String, bool>,
) -> io::Result<()> {
    if config.ai.protocol != "unconfigured"
        && !local_no_auth(&config.ai.base_url)
        && !secrets.get("ai.api_key").copied().unwrap_or(false)
    {
        return Err(invalid("this AI provider requires an API key"));
    }
    if config.surfaces.slack_socket_mode
        && !secrets
            .get("surfaces.slack_app_token")
            .copied()
            .unwrap_or(false)
    {
        return Err(invalid("Slack Socket Mode requires an app token"));
    }
    if config.surfaces.telegram_polling
        && !secrets
            .get("surfaces.telegram_bot_token")
            .copied()
            .unwrap_or(false)
    {
        return Err(invalid("Telegram polling requires a bot token"));
    }
    Ok(())
}

pub(crate) fn provider_profile_changed(old: &AiProfile, new: &AiProfile) -> bool {
    let mut old = old.clone();
    let mut new = new.clone();
    old.last_validated_at_unix_ms = None;
    new.last_validated_at_unix_ms = None;
    old != new
}

pub(crate) fn validate_secret_changes(
    changes: &BTreeMap<String, Option<String>>,
) -> io::Result<()> {
    for (name, value) in changes {
        if !SECRET_NAMES.contains(&name.as_str()) {
            return Err(invalid(format!("unknown secret field {name:?}")));
        }
        if let Some(value) = value {
            validate_text(name, value, 16 * 1024)?;
            validate_vault_capacity(name, value)?;
        }
    }
    Ok(())
}

pub(crate) fn valid_http_url(value: &str) -> bool {
    let Some((scheme, rest)) = value.split_once("://") else {
        return false;
    };
    matches!(scheme, "http" | "https")
        && !rest.is_empty()
        && !rest.contains('@')
        && !rest.bytes().any(|byte| byte.is_ascii_whitespace())
}

pub(crate) fn local_no_auth(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    ["http://127.0.0.1:", "http://localhost:", "http://[::1]:"]
        .iter()
        .any(|prefix| lower.starts_with(prefix))
}

pub(crate) fn validate_text(label: &str, value: &str, maximum: usize) -> io::Result<()> {
    if value.len() > maximum || value.chars().any(char::is_control) {
        return Err(invalid(format!(
            "{label} contains invalid characters or is too long"
        )));
    }
    Ok(())
}

pub(crate) fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}
