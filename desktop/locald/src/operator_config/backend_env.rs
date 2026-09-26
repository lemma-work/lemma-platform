//! What the backend is started with.

use super::*;

pub(crate) fn secret_environment() -> [(&'static str, &'static str); 20] {
    [
        ("integrations.deepgram_api_key", "DEEPGRAM_API_KEY"),
        ("integrations.brave_search_api_key", "BRAVE_SEARCH_API_KEY"),
        ("integrations.composio_api_key", "COMPOSIO_API_KEY"),
        (
            "integrations.composio_webhook_secret",
            "COMPOSIO_WEBHOOK_SECRET",
        ),
        (
            "integrations.google_client_secret",
            "CONNECTOR_GOOGLE_CLIENT_SECRET",
        ),
        (
            "integrations.microsoft_client_secret",
            "CONNECTOR_MICROSOFT_CLIENT_SECRET",
        ),
        (
            "integrations.github_client_secret",
            "CONNECTOR_GITHUB_CLIENT_SECRET",
        ),
        ("integrations.slack_client_secret", "SLACK_CLIENT_SECRET"),
        ("surfaces.slack_app_token", "SLACK_APP_TOKEN"),
        ("surfaces.slack_bot_token", "SLACK_BOT_TOKEN"),
        ("surfaces.slack_signing_secret", "SLACK_SIGNING_SECRET"),
        ("surfaces.telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
        (
            "surfaces.telegram_webhook_secret",
            "TELEGRAM_WEBHOOK_SECRET",
        ),
        ("surfaces.teams_app_password", "MICROSOFT_BOT_APP_PASSWORD"),
        ("surfaces.whatsapp_access_token", "WHATSAPP_ACCESS_TOKEN"),
        ("surfaces.whatsapp_verify_token", "WHATSAPP_VERIFY_TOKEN"),
        ("surfaces.whatsapp_app_secret", "WHATSAPP_APP_SECRET"),
        ("surfaces.resend_api_key", "RESEND_API_KEY"),
        // One secret for the Resend webhook that carries inbound email and
        // notification replies. Was RESEND_INBOUND_SIGNING_SECRET; the backend
        // still accepts that name as an alias, but new deployments set this.
        ("surfaces.resend_signing_secret", "RESEND_WEBHOOK_SECRET"),
        // Harmless on its own: the backend reads it only with the SMTP host,
        // user and sender, which `email_environment` sets only for SMTP.
        ("email.smtp_password", "SMTP_PASSWORD"),
    ]
}

/// Secrets the frontend's own server reads, never the backend: live voice
/// calls run through the workspace server's voice gateway (Gemini Live) and
/// its call router (TypeSafe), not through the API.
pub(crate) fn frontend_secret_environment() -> [(&'static str, &'static str); 2] {
    [
        ("integrations.gemini_api_key", "GEMINI_API_KEY"),
        ("integrations.typesafe_api_key", "TYPESAFE_API_KEY"),
    ]
}

/// The models the backend's side jobs run on, all on the system profile.
///
/// `VISION_MODEL` names the model that reads images for a teammate whose own
/// model cannot; it must be one the profile declares image-capable, which on
/// the OpenAI-compatible protocol means listed in its vision names (`apply`
/// adds the chosen image model there). Anthropic's models all read images.
/// Titles use the fast model, or the default when there is none, because the
/// backend makes no LLM titles at all while `CONVERSATION_TITLE_MODEL` is
/// unset. Summaries fall back to the run's own model by themselves, so they
/// are named only when a fast model is.
pub(crate) fn ai_side_job_environment(ai: &AiProfile) -> Vec<(&'static str, String)> {
    let mut environment = Vec::new();
    if ai.protocol == "unconfigured" || ai.default_model.is_empty() {
        return environment;
    }
    let reads_images = |model: &str| {
        ai.protocol == "anthropic_compat" || ai.vision_models.iter().any(|one| one == model)
    };
    let image_model = if !ai.image_model.is_empty() && reads_images(&ai.image_model) {
        Some(ai.image_model.clone())
    } else if reads_images(&ai.default_model) {
        Some(ai.default_model.clone())
    } else {
        None
    };
    if let Some(model) = image_model {
        environment.push(("VISION_MODEL", model));
    }
    let fast = (!ai.fast_model.is_empty()).then(|| ai.fast_model.clone());
    environment.push((
        "CONVERSATION_TITLE_MODEL",
        fast.clone().unwrap_or_else(|| ai.default_model.clone()),
    ));
    if let Some(model) = fast {
        environment.push(("HISTORY_SUMMARIZATION_MODEL", model));
    }
    environment
}

/// Whether the email section names a complete way to send, given which of
/// its credentials are stored. The Resend key is the surfaces section's: one
/// Resend account carries both inbound and outgoing mail.
pub(crate) fn email_ready(email: &EmailConfig, resend_key: bool, smtp_password: bool) -> bool {
    match email.provider.as_str() {
        "resend" => resend_key && !email.from_email.trim().is_empty(),
        "smtp" => {
            smtp_password
                && !email.from_email.trim().is_empty()
                && !email.smtp_host.trim().is_empty()
                && !email.smtp_user.trim().is_empty()
        }
        _ => false,
    }
}

/// What the backend sends mail with. Nothing when mail is not set up, which
/// leaves the host pack's local spool in place.
pub(crate) fn email_environment(
    email: &EmailConfig,
    resend_key: bool,
    smtp_password: bool,
) -> Vec<(&'static str, String)> {
    if !email_ready(email, resend_key, smtp_password) {
        return Vec::new();
    }
    let from = email.from_email.trim().to_owned();
    match email.provider.as_str() {
        // The backend relays through Resend's SMTP endpoint with the API key
        // as the password (`EmailSender.from_settings`).
        "resend" => vec![
            ("EMAIL_TRANSPORT", "smtp".into()),
            ("RESEND_FROM_EMAIL", from),
        ],
        _ => vec![
            ("EMAIL_TRANSPORT", "smtp".into()),
            ("SMTP_HOST", email.smtp_host.trim().to_owned()),
            ("SMTP_PORT", email.smtp_port.to_string()),
            ("SMTP_USER", email.smtp_user.trim().to_owned()),
            ("SMTP_FROM_EMAIL", from),
            ("SMTP_USE_TLS", email.smtp_use_tls.to_string()),
        ],
    }
}

pub(crate) fn insert_nonempty(environment: &mut HashMap<String, String>, key: &str, value: &str) {
    if !value.is_empty() {
        environment.insert(key.into(), value.into());
    }
}

/// A Fernet key: 32 random bytes as url-safe base64, which is what the
/// backend's `parse_keyset` expects each entry's `key` to be.
pub(crate) fn fernet_key() -> io::Result<String> {
    use base64::engine::general_purpose::URL_SAFE;
    use base64::Engine;

    let mut random = [0_u8; 32];
    getrandom::fill(&mut random)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(URL_SAFE.encode(random))
}

pub(crate) fn random_hex(bytes: usize) -> io::Result<String> {
    let mut random = vec![0_u8; bytes];
    getrandom::fill(&mut random)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(random.iter().map(|byte| format!("{byte:02x}")).collect())
}

pub(crate) fn current_unix_ms() -> io::Result<u64> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| io::Error::other(format!("system clock is before Unix epoch: {error}")))?;
    u64::try_from(duration.as_millis())
        .map_err(|_| io::Error::other("system time does not fit in milliseconds"))
}

impl OperatorConfigStore {
    /// What the frontend is started with on top of the host pack's
    /// environment: only the voice-call keys, when stored.
    pub fn frontend_environment(&self) -> io::Result<HashMap<String, String>> {
        let install_id = self
            .config
            .lock()
            .expect("operator config poisoned")
            .install_id
            .clone();
        let mut environment = HashMap::new();
        for (secret, variable) in frontend_secret_environment() {
            if let Some(value) = self.vault.get(&install_id, secret)? {
                if !value.is_empty() {
                    environment.insert(variable.into(), value);
                }
            }
        }
        Ok(environment)
    }

    pub fn backend_environment(&self) -> io::Result<HashMap<String, String>> {
        let config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        let mut environment = HashMap::new();
        // Applied over the host pack's own environment, so this is what decides
        // how the backend gets its keys regardless of what the manifest says.
        environment.insert("SECRET_KEY_PROVIDER".into(), "static".into());
        environment.insert(
            "SECRET_ENCRYPTION_KEYSET".into(),
            self.secret_encryption_keyset(&config.install_id)?,
        );
        // One lookup, used for both the readiness flag and the provider key
        // below. `secret_presence` would answer the same question by reading
        // every secret this install has, and each of those is a separate vault
        // access on the daemon's start path.
        let ai_api_key = self.vault.get(&config.install_id, "ai.api_key")?;
        environment.insert(
            "LEMMA_LOCAL_AI_READY".into(),
            ai_ready(
                &config,
                ai_api_key.as_deref().is_some_and(|value| !value.is_empty()),
            )
            .to_string(),
        );
        match config.ai.protocol.as_str() {
            "openai_compat" => {
                environment.insert("LEMMA_DEFAULT_MODEL_TYPE".into(), "openai_compat".into());
                environment.insert("LEMMA_OPENAI_BASE_URL".into(), config.ai.base_url.clone());
                environment.insert(
                    "LEMMA_OPENAI_DEFAULT_MODEL".into(),
                    config.ai.default_model.clone(),
                );
                environment.insert(
                    "LEMMA_OPENAI_MODEL_NAMES".into(),
                    config.ai.models.join(","),
                );
                if !config.ai.vision_models.is_empty() {
                    environment.insert(
                        "LEMMA_OPENAI_VISION_MODEL_NAMES".into(),
                        config.ai.vision_models.join(","),
                    );
                }
                let api_key = ai_api_key
                    .clone()
                    .or_else(|| local_no_auth(&config.ai.base_url).then(|| "lemma-local".into()));
                if let Some(api_key) = api_key {
                    environment.insert("LEMMA_OPENAI_API_KEY".into(), api_key);
                }
            }
            "anthropic_compat" => {
                environment.insert("LEMMA_DEFAULT_MODEL_TYPE".into(), "anthropic_compat".into());
                environment.insert(
                    "LEMMA_ANTHROPIC_BASE_URL".into(),
                    config.ai.base_url.clone(),
                );
                environment.insert(
                    "LEMMA_ANTHROPIC_DEFAULT_MODEL".into(),
                    config.ai.default_model.clone(),
                );
                environment.insert(
                    "LEMMA_ANTHROPIC_MODEL_NAMES".into(),
                    config.ai.models.join(","),
                );
                if let Some(api_key) = ai_api_key.clone() {
                    environment.insert("LEMMA_ANTHROPIC_API_KEY".into(), api_key);
                }
            }
            _ => {}
        }
        for (key, value) in ai_side_job_environment(&config.ai) {
            environment.insert(key.into(), value);
        }

        insert_nonempty(
            &mut environment,
            "CONNECTOR_GOOGLE_CLIENT_ID",
            &config.integrations.google_client_id,
        );
        insert_nonempty(
            &mut environment,
            "CONNECTOR_MICROSOFT_CLIENT_ID",
            &config.integrations.microsoft_client_id,
        );
        insert_nonempty(
            &mut environment,
            "CONNECTOR_GITHUB_CLIENT_ID",
            &config.integrations.github_client_id,
        );
        insert_nonempty(
            &mut environment,
            "SLACK_CLIENT_ID",
            &config.integrations.slack_client_id,
        );
        insert_nonempty(
            &mut environment,
            "MICROSOFT_BOT_APP_ID",
            &config.surfaces.teams_app_id,
        );
        insert_nonempty(
            &mut environment,
            "MICROSOFT_BOT_TENANT_ID",
            &config.surfaces.teams_tenant_id,
        );
        insert_nonempty(
            &mut environment,
            "WHATSAPP_PHONE_NUMBER_ID",
            &config.surfaces.whatsapp_phone_number_id,
        );
        insert_nonempty(
            &mut environment,
            "WHATSAPP_WABA_ID",
            &config.surfaces.whatsapp_waba_id,
        );
        insert_nonempty(
            &mut environment,
            "RESEND_INBOUND_DOMAIN",
            &config.surfaces.resend_inbound_domain,
        );
        for (secret, variable) in secret_environment() {
            if secret == "ai.api_key" {
                continue;
            }
            if let Some(value) = self.vault.get(&config.install_id, secret)? {
                environment.insert(variable.into(), value);
            }
        }
        let has = |variable: &str| {
            environment
                .get(variable)
                .is_some_and(|value: &String| !value.trim().is_empty())
        };
        let slack_app_token = has("SLACK_APP_TOKEN");
        let telegram_bot_token = has("TELEGRAM_BOT_TOKEN");
        let brave_key = has("BRAVE_SEARCH_API_KEY");
        let resend_key = has("RESEND_API_KEY");
        let smtp_password = has("SMTP_PASSWORD");
        // This Mac has no public address for Slack's or Telegram's webhooks
        // unless it is shared publicly, so a bot that is set up at all listens
        // the way that needs none. The switches stay for an install that set
        // them explicitly before the bot's token was saved.
        let slack_socket = config.surfaces.slack_socket_mode || slack_app_token;
        let telegram_polling = config.surfaces.telegram_polling || telegram_bot_token;
        environment.insert("ENABLE_SLACK_SOCKET_MODE".into(), slack_socket.to_string());
        environment.insert(
            "ENABLE_TELEGRAM_POLLING_MODE".into(),
            telegram_polling.to_string(),
        );
        if brave_key {
            environment.insert("WEB_SEARCH_PROVIDER".into(), "brave".into());
        }
        for (key, value) in email_environment(&config.email, resend_key, smtp_password) {
            environment.insert(key.into(), value);
        }
        Ok(environment)
    }
}
