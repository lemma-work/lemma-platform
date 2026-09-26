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
    for (label, model) in [
        ("image model", &config.ai.image_model),
        ("fast model", &config.ai.fast_model),
    ] {
        if !model.is_empty() && !config.ai.models.contains(model) {
            return Err(invalid(format!(
                "the {label} must be one of the provider's models"
            )));
        }
    }
    Ok(())
}

/// Choosing a model to read images is saying it can.
///
/// The OpenAI-compatible model list does not report modalities, so the
/// backend declares a model image-capable only when it is named in the vision
/// list; the chosen image model is added there rather than left for the
/// person to also tick. Anthropic's models all read images.
pub(crate) fn declare_image_model(ai: &mut AiProfile) {
    if ai.protocol == "openai_compat"
        && !ai.image_model.is_empty()
        && !ai.vision_models.contains(&ai.image_model)
    {
        ai.vision_models.push(ai.image_model.clone());
    }
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
        ("sender address", &config.email.from_email),
        ("SMTP host", &config.email.smtp_host),
        ("SMTP user", &config.email.smtp_user),
    ] {
        validate_text(label, value, 2048)?;
    }
    validate_email_shape(&config.email)
}

pub(crate) fn validate_email_shape(email: &EmailConfig) -> io::Result<()> {
    if !matches!(email.provider.as_str(), "none" | "resend" | "smtp") {
        return Err(invalid("unsupported email provider"));
    }
    let from = email.from_email.trim();
    if !from.is_empty() && (!from.contains('@') || from.contains(char::is_whitespace)) {
        return Err(invalid("the sender must be an email address"));
    }
    if email.smtp_host.contains(char::is_whitespace)
        || email.smtp_host.contains('/')
        || email.smtp_host.contains(':')
    {
        return Err(invalid(
            "the SMTP host is a host name only, without a scheme or port",
        ));
    }
    if email.smtp_port == 0 {
        return Err(invalid("the SMTP port must be a port number"));
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
    validate_text("image model", &ai.image_model, 256)?;
    validate_text("fast model", &ai.fast_model, 256)?;
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
    let stored = |name: &str| secrets.get(name).copied().unwrap_or(false);
    let email = &config.email;
    match email.provider.as_str() {
        "resend" if !stored("surfaces.resend_api_key") => {
            return Err(invalid("sending with Resend requires a Resend API key"));
        }
        "smtp"
            if email.smtp_host.trim().is_empty()
                || email.smtp_user.trim().is_empty()
                || !stored("email.smtp_password") =>
        {
            return Err(invalid(
                "sending with SMTP requires a host, a user name and a password",
            ));
        }
        "resend" | "smtp" if email.from_email.trim().is_empty() => {
            return Err(invalid("sending email requires a sender address"));
        }
        _ => {}
    }
    Ok(())
}

/// Whether a change needs the provider asked again.
///
/// Where the requests go and what they ask for by default. Choosing a
/// different image or fast model from the list the last probe returned is
/// not a new provider, and re-probing for it would make a model picker wait
/// on the network -- or fail for a provider that is briefly unreachable.
pub(crate) fn provider_profile_changed(old: &AiProfile, new: &AiProfile) -> bool {
    old.protocol != new.protocol
        || old.base_url != new.base_url
        || old.allow_private_network != new.allow_private_network
        || old.default_model != new.default_model
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
