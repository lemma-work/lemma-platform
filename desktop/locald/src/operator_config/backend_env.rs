//! What the backend is started with.

use super::*;

pub(crate) fn secret_environment() -> [(&'static str, &'static str); 18] {
    [
        ("integrations.deepgram_api_key", "DEEPGRAM_API_KEY"),
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
    ]
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
        environment.insert(
            "ENABLE_SLACK_SOCKET_MODE".into(),
            config.surfaces.slack_socket_mode.to_string(),
        );
        environment.insert(
            "ENABLE_TELEGRAM_POLLING_MODE".into(),
            config.surfaces.telegram_polling.to_string(),
        );

        for (secret, variable) in secret_environment() {
            if secret == "ai.api_key" {
                continue;
            }
            if let Some(value) = self.vault.get(&config.install_id, secret)? {
                environment.insert(variable.into(), value);
            }
        }
        Ok(environment)
    }
}
