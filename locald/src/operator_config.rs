use std::collections::{BTreeMap, HashMap};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::provider_probe::{HttpModelProviderProbe, ModelProviderProbe};

const CONFIG_SCHEMA_VERSION: u64 = 1;
const VAULT_SERVICE: &str = "work.lemma.local";

const SECRET_NAMES: [&str; 16] = [
    "ai.api_key",
    "integrations.composio_api_key",
    "integrations.composio_webhook_secret",
    "integrations.google_client_secret",
    "integrations.microsoft_client_secret",
    "surfaces.slack_app_token",
    "surfaces.slack_bot_token",
    "surfaces.slack_signing_secret",
    "surfaces.telegram_bot_token",
    "surfaces.telegram_webhook_secret",
    "surfaces.teams_app_password",
    "surfaces.whatsapp_access_token",
    "surfaces.whatsapp_verify_token",
    "surfaces.whatsapp_app_secret",
    "surfaces.resend_api_key",
    "surfaces.resend_signing_secret",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OperatorConfig {
    pub schema_version: u64,
    pub install_id: String,
    pub revision: u64,
    pub onboarding_complete: bool,
    pub ai: AiProfile,
    pub integrations: IntegrationConfig,
    pub surfaces: SurfaceConfig,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AiProfile {
    pub protocol: String,
    pub base_url: String,
    pub default_model: String,
    pub models: Vec<String>,
    pub vision_models: Vec<String>,
    #[serde(default)]
    pub allow_private_network: bool,
    #[serde(default)]
    pub last_validated_at_unix_ms: Option<u64>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IntegrationConfig {
    pub composio_enabled: bool,
    pub google_client_id: String,
    pub microsoft_client_id: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SurfaceConfig {
    pub slack_socket_mode: bool,
    pub telegram_polling: bool,
    pub teams_app_id: String,
    pub teams_tenant_id: String,
    pub whatsapp_phone_number_id: String,
    pub whatsapp_waba_id: String,
    pub resend_inbound_domain: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplyOperatorConfig {
    pub config: OperatorConfig,
    #[serde(default)]
    pub secrets: BTreeMap<String, Option<String>>,
}

impl OperatorConfig {
    fn fresh() -> io::Result<Self> {
        Ok(Self {
            schema_version: CONFIG_SCHEMA_VERSION,
            install_id: random_hex(16)?,
            revision: 0,
            onboarding_complete: false,
            ai: AiProfile {
                protocol: "unconfigured".into(),
                ..Default::default()
            },
            integrations: IntegrationConfig::default(),
            surfaces: SurfaceConfig {
                resend_inbound_domain: "".into(),
                ..Default::default()
            },
        })
    }
}

trait SecretVault: Send + Sync {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>>;
    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()>;
    fn delete(&self, install_id: &str, name: &str) -> io::Result<()>;
}

struct PlatformVault;

impl PlatformVault {
    fn entry(install_id: &str, name: &str) -> io::Result<keyring::v1::Entry> {
        keyring::v1::Entry::new(VAULT_SERVICE, &format!("{install_id}:{name}")).map_err(vault_error)
    }
}

impl SecretVault for PlatformVault {
    fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
        match Self::entry(install_id, name)?.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::v1::Error::NoEntry) => Ok(None),
            Err(error) => Err(vault_error(error)),
        }
    }

    fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
        Self::entry(install_id, name)?
            .set_password(value)
            .map_err(vault_error)
    }

    fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
        match Self::entry(install_id, name)?.delete_credential() {
            Ok(()) | Err(keyring::v1::Error::NoEntry) => Ok(()),
            Err(error) => Err(vault_error(error)),
        }
    }
}

fn vault_error(error: keyring::v1::Error) -> io::Error {
    io::Error::other(format!("operating-system credential vault failed: {error}"))
}

pub struct OperatorConfigStore {
    path: PathBuf,
    vault: Arc<dyn SecretVault>,
    provider_probe: Arc<dyn ModelProviderProbe>,
    config: Mutex<OperatorConfig>,
}

#[derive(Clone)]
pub(crate) struct OperatorConfigState {
    config: OperatorConfig,
    secrets: BTreeMap<String, Option<String>>,
}

impl OperatorConfigStore {
    pub fn load(path: PathBuf) -> io::Result<Arc<Self>> {
        Self::load_components(
            path,
            Arc::new(PlatformVault),
            Arc::new(HttpModelProviderProbe),
        )
    }

    #[cfg(test)]
    fn load_with_vault(path: PathBuf, vault: Arc<dyn SecretVault>) -> io::Result<Arc<Self>> {
        Self::load_components(path, vault, Arc::new(EchoModelProviderProbe))
    }

    fn load_components(
        path: PathBuf,
        vault: Arc<dyn SecretVault>,
        provider_probe: Arc<dyn ModelProviderProbe>,
    ) -> io::Result<Arc<Self>> {
        let config = if path.is_file() {
            ensure_private_file(&path)?;
            serde_json::from_slice(&fs::read(&path)?).map_err(|error| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("invalid operator configuration: {error}"),
                )
            })?
        } else {
            let config = OperatorConfig::fresh()?;
            validate_config(&config)?;
            write_private_atomic(&path, &serde_json::to_vec_pretty(&config)?)?;
            config
        };
        validate_config(&config)?;
        Ok(Arc::new(Self {
            path,
            vault,
            provider_probe,
            config: Mutex::new(config),
        }))
    }

    pub fn snapshot(&self) -> io::Result<Value> {
        let config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        let secret_presence = self.secret_presence(&config)?;
        Ok(snapshot_value(config, secret_presence))
    }

    pub fn apply(&self, mut request: ApplyOperatorConfig) -> io::Result<Value> {
        let old_config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        if request.config.install_id != old_config.install_id {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "operator config install identity cannot be changed",
            ));
        }
        request.config.schema_version = CONFIG_SCHEMA_VERSION;
        request.config.revision = old_config.revision.saturating_add(1);
        validate_secret_changes(&request.secrets)?;
        validate_config_shape(&request.config)?;
        let provider_changed = provider_profile_changed(&old_config.ai, &request.config.ai)
            || request.secrets.contains_key("ai.api_key")
            || request.config.ai.last_validated_at_unix_ms.is_none();
        if request.config.ai.protocol == "unconfigured" {
            request.config.ai.last_validated_at_unix_ms = None;
        } else if provider_changed {
            let api_key = match request.secrets.get("ai.api_key") {
                Some(Some(value)) if !value.is_empty() => Some(value.clone()),
                Some(_) => None,
                None => self.vault.get(&old_config.install_id, "ai.api_key")?,
            };
            if !local_no_auth(&request.config.ai.base_url) && api_key.is_none() {
                return Err(invalid("this AI provider requires an API key"));
            }
            let models = self
                .provider_probe
                .discover(&request.config.ai, api_key.as_deref())?;
            if request.config.ai.default_model.is_empty() {
                request.config.ai.default_model = models[0].clone();
            }
            if !models.contains(&request.config.ai.default_model) {
                return Err(invalid(format!(
                    "default model {:?} was not returned by the provider",
                    request.config.ai.default_model
                )));
            }
            request.config.ai.models = models;
            request.config.ai.last_validated_at_unix_ms = Some(current_unix_ms()?);
        } else {
            request.config.ai.last_validated_at_unix_ms = old_config.ai.last_validated_at_unix_ms;
        }
        validate_config(&request.config)?;

        let old_secrets =
            self.read_secrets(&old_config, request.secrets.keys().map(String::as_str))?;
        for (name, value) in &request.secrets {
            if let Err(error) = set_secret(
                self.vault.as_ref(),
                &old_config.install_id,
                name,
                value.as_deref().filter(|value| !value.is_empty()),
            ) {
                return Err(with_restore_error(
                    error,
                    restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
                ));
            }
        }

        let presence = match self.secret_presence(&request.config) {
            Ok(presence) => presence,
            Err(error) => {
                return Err(with_restore_error(
                    error,
                    restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
                ));
            }
        };
        if let Err(error) = validate_capability_requirements(&request.config, &presence) {
            return Err(with_restore_error(
                error,
                restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
            ));
        }
        if let Err(error) =
            write_private_atomic(&self.path, &serde_json::to_vec_pretty(&request.config)?)
        {
            return Err(with_restore_error(
                error,
                restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
            ));
        }
        let config = request.config;
        *self.config.lock().expect("operator config poisoned") = config.clone();
        Ok(snapshot_value(config, presence))
    }

    pub(crate) fn capture_state(&self) -> io::Result<OperatorConfigState> {
        let config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        Ok(OperatorConfigState {
            secrets: self.read_secrets(&config, SECRET_NAMES.into_iter())?,
            config,
        })
    }

    pub(crate) fn restore_state(&self, state: OperatorConfigState) -> io::Result<Value> {
        validate_config(&state.config)?;
        let current = self.capture_state()?;
        if let Err(error) = restore_secrets(
            self.vault.as_ref(),
            &state.config.install_id,
            &state.secrets,
        ) {
            return Err(with_restore_error(
                error,
                restore_secrets(
                    self.vault.as_ref(),
                    &current.config.install_id,
                    &current.secrets,
                ),
            ));
        }
        let presence = match self.secret_presence(&state.config) {
            Ok(presence) => presence,
            Err(error) => {
                return Err(with_restore_error(
                    error,
                    restore_secrets(
                        self.vault.as_ref(),
                        &current.config.install_id,
                        &current.secrets,
                    ),
                ));
            }
        };
        if let Err(error) =
            write_private_atomic(&self.path, &serde_json::to_vec_pretty(&state.config)?)
        {
            return Err(with_restore_error(
                error,
                restore_secrets(
                    self.vault.as_ref(),
                    &current.config.install_id,
                    &current.secrets,
                ),
            ));
        }
        *self.config.lock().expect("operator config poisoned") = state.config.clone();
        Ok(snapshot_value(state.config, presence))
    }

    pub fn backend_environment(&self) -> io::Result<HashMap<String, String>> {
        let config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        let mut environment = HashMap::new();
        let secret_presence = self.secret_presence(&config)?;
        environment.insert(
            "LEMMA_LOCAL_AI_READY".into(),
            (readiness(&config, &secret_presence)["ai"] == "ready").to_string(),
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
                let api_key = self
                    .vault
                    .get(&config.install_id, "ai.api_key")?
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
                if let Some(api_key) = self.vault.get(&config.install_id, "ai.api_key")? {
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

    pub(crate) fn configure_local_openai(
        &self,
        base_url: String,
        model: String,
    ) -> io::Result<Value> {
        let mut config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        config.ai = AiProfile {
            protocol: "openai_compat".into(),
            base_url,
            default_model: model.clone(),
            models: vec![model],
            vision_models: Vec::new(),
            allow_private_network: false,
            last_validated_at_unix_ms: None,
        };
        self.apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::from([("ai.api_key".into(), None)]),
        })
    }

    pub(crate) fn clear_local_openai_if_base(&self, base_url: &str) -> io::Result<Value> {
        let mut config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        if !config
            .ai
            .base_url
            .trim_end_matches('/')
            .eq_ignore_ascii_case(base_url.trim_end_matches('/'))
        {
            return self.snapshot();
        }
        config.ai = AiProfile {
            protocol: "unconfigured".into(),
            ..AiProfile::default()
        };
        self.apply(ApplyOperatorConfig {
            config,
            secrets: BTreeMap::from([("ai.api_key".into(), None)]),
        })
    }

    fn secret_presence(&self, config: &OperatorConfig) -> io::Result<BTreeMap<String, bool>> {
        SECRET_NAMES
            .iter()
            .map(|name| {
                self.vault.get(&config.install_id, name).map(|value| {
                    (
                        (*name).to_owned(),
                        value.is_some_and(|value| !value.is_empty()),
                    )
                })
            })
            .collect()
    }

    fn read_secrets<'a>(
        &self,
        config: &OperatorConfig,
        names: impl Iterator<Item = &'a str>,
    ) -> io::Result<BTreeMap<String, Option<String>>> {
        names
            .map(|name| {
                self.vault
                    .get(&config.install_id, name)
                    .map(|value| (name.to_owned(), value))
            })
            .collect()
    }
}

fn validate_config(config: &OperatorConfig) -> io::Result<()> {
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

fn validate_config_shape(config: &OperatorConfig) -> io::Result<()> {
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
    if !matches!(
        config.ai.protocol.as_str(),
        "unconfigured" | "openai_compat" | "anthropic_compat"
    ) {
        return Err(invalid("unsupported AI provider protocol"));
    }
    for (label, value) in [
        ("AI base URL", &config.ai.base_url),
        ("AI default model", &config.ai.default_model),
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
    for model in config.ai.models.iter().chain(&config.ai.vision_models) {
        validate_text("model name", model, 256)?;
    }
    if config.ai.protocol != "unconfigured" && !valid_http_url(&config.ai.base_url) {
        return Err(invalid(
            "AI base URL must use HTTP or HTTPS without embedded credentials",
        ));
    }
    Ok(())
}

fn validate_capability_requirements(
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

fn provider_profile_changed(old: &AiProfile, new: &AiProfile) -> bool {
    let mut old = old.clone();
    let mut new = new.clone();
    old.last_validated_at_unix_ms = None;
    new.last_validated_at_unix_ms = None;
    old != new
}

fn validate_secret_changes(changes: &BTreeMap<String, Option<String>>) -> io::Result<()> {
    for (name, value) in changes {
        if !SECRET_NAMES.contains(&name.as_str()) {
            return Err(invalid(format!("unknown secret field {name:?}")));
        }
        if let Some(value) = value {
            validate_text(name, value, 16 * 1024)?;
        }
    }
    Ok(())
}

fn configuration_schema() -> Value {
    json!({
        "version": CONFIG_SCHEMA_VERSION,
        "groups": [
            {"id":"overview","label":"Overview"},
            {"id":"ai","label":"AI Providers","required":true,"restart_scope":["backend"]},
            {"id":"integrations","label":"Integrations","required":false,"restart_scope":["backend"]},
            {"id":"surfaces","label":"Agent Surfaces","required":false,"restart_scope":["backend"]},
            {"id":"services","label":"Services"},
            {"id":"diagnostics","label":"Diagnostics"}
        ],
        "secret_storage":"os-vault",
        "providers":["openai_compat","anthropic_compat"],
        "local_detection":[
            {"id":"ollama","base_url":"http://127.0.0.1:11434/v1"},
            {"id":"lm_studio","base_url":"http://127.0.0.1:1234/v1"}
        ]
    })
}

fn snapshot_value(config: OperatorConfig, secret_presence: BTreeMap<String, bool>) -> Value {
    json!({
        "schema": configuration_schema(),
        "readiness": readiness(&config, &secret_presence),
        "config": config,
        "secrets": secret_presence,
    })
}

fn readiness(config: &OperatorConfig, secrets: &BTreeMap<String, bool>) -> Value {
    let ai_ready = config.ai.protocol != "unconfigured"
        && !config.ai.default_model.is_empty()
        && config.ai.last_validated_at_unix_ms.is_some()
        && (local_no_auth(&config.ai.base_url)
            || secrets.get("ai.api_key").copied().unwrap_or(false));
    json!({
        "ai": if ai_ready { "ready" } else { "needs_setup" },
        "integrations": if config.integrations.composio_enabled
            || !config.integrations.google_client_id.is_empty()
            || !config.integrations.microsoft_client_id.is_empty()
        { "configured" } else { "optional" },
        "surfaces": if config.surfaces.slack_socket_mode
            || config.surfaces.telegram_polling
            || !config.surfaces.teams_app_id.is_empty()
            || !config.surfaces.whatsapp_phone_number_id.is_empty()
        { "configured" } else { "optional" },
        "overall": if ai_ready { "ready" } else { "needs_ai_setup" },
    })
}

fn secret_environment() -> [(&'static str, &'static str); 15] {
    [
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
        (
            "surfaces.resend_signing_secret",
            "RESEND_INBOUND_SIGNING_SECRET",
        ),
    ]
}

fn restore_secrets(
    vault: &dyn SecretVault,
    install_id: &str,
    previous: &BTreeMap<String, Option<String>>,
) -> io::Result<()> {
    let mut first_error = None;
    for (name, value) in previous {
        if let Err(error) = set_secret(vault, install_id, name, value.as_deref()) {
            first_error.get_or_insert(error);
        }
    }
    match first_error {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

fn set_secret(
    vault: &dyn SecretVault,
    install_id: &str,
    name: &str,
    value: Option<&str>,
) -> io::Result<()> {
    match value {
        Some(value) => vault.set(install_id, name, value),
        None => vault.delete(install_id, name),
    }
}

fn with_restore_error(error: io::Error, restore: io::Result<()>) -> io::Error {
    match restore {
        Ok(()) => error,
        Err(restore_error) => io::Error::other(format!(
            "{error}; restoring the previous credential state also failed: {restore_error}"
        )),
    }
}

fn valid_http_url(value: &str) -> bool {
    let Some((scheme, rest)) = value.split_once("://") else {
        return false;
    };
    matches!(scheme, "http" | "https")
        && !rest.is_empty()
        && !rest.contains('@')
        && !rest.bytes().any(|byte| byte.is_ascii_whitespace())
}

fn local_no_auth(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    ["http://127.0.0.1:", "http://localhost:", "http://[::1]:"]
        .iter()
        .any(|prefix| lower.starts_with(prefix))
}

fn validate_text(label: &str, value: &str, maximum: usize) -> io::Result<()> {
    if value.len() > maximum || value.chars().any(char::is_control) {
        return Err(invalid(format!(
            "{label} contains invalid characters or is too long"
        )));
    }
    Ok(())
}

fn insert_nonempty(environment: &mut HashMap<String, String>, key: &str, value: &str) {
    if !value.is_empty() {
        environment.insert(key.into(), value.into());
    }
}

fn random_hex(bytes: usize) -> io::Result<String> {
    let mut random = vec![0_u8; bytes];
    getrandom::fill(&mut random)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(random.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn current_unix_ms() -> io::Result<u64> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| io::Error::other(format!("system clock is before Unix epoch: {error}")))?;
    u64::try_from(duration.as_millis())
        .map_err(|_| io::Error::other("system time does not fit in milliseconds"))
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "config path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension(format!("next-{}", std::process::id()));
    let _ = fs::remove_file(&temporary);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    ensure_private_file(path)
}

fn ensure_private_file(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!("operator config is not a private file: {}", path.display()),
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

#[cfg(test)]
struct EchoModelProviderProbe;

#[cfg(test)]
impl ModelProviderProbe for EchoModelProviderProbe {
    fn discover(&self, profile: &AiProfile, _api_key: Option<&str>) -> io::Result<Vec<String>> {
        if profile.models.is_empty() {
            Err(invalid("test provider has no models"))
        } else {
            Ok(profile.models.clone())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[derive(Default)]
    struct MemoryVault(Mutex<BTreeMap<String, String>>);

    impl SecretVault for MemoryVault {
        fn get(&self, install_id: &str, name: &str) -> io::Result<Option<String>> {
            Ok(self
                .0
                .lock()
                .unwrap()
                .get(&format!("{install_id}:{name}"))
                .cloned())
        }

        fn set(&self, install_id: &str, name: &str, value: &str) -> io::Result<()> {
            self.0
                .lock()
                .unwrap()
                .insert(format!("{install_id}:{name}"), value.into());
            Ok(())
        }

        fn delete(&self, install_id: &str, name: &str) -> io::Result<()> {
            self.0
                .lock()
                .unwrap()
                .remove(&format!("{install_id}:{name}"));
            Ok(())
        }
    }

    struct FixedModelProviderProbe;

    impl ModelProviderProbe for FixedModelProviderProbe {
        fn discover(
            &self,
            _profile: &AiProfile,
            _api_key: Option<&str>,
        ) -> io::Result<Vec<String>> {
            Ok(vec!["alpha-model".into(), "zeta-model".into()])
        }
    }

    #[test]
    fn successful_provider_probe_discovers_default_and_marks_profile_ready() {
        let root = tempdir().unwrap();
        let store = OperatorConfigStore::load_components(
            root.path().join("operator.json"),
            Arc::new(MemoryVault::default()),
            Arc::new(FixedModelProviderProbe),
        )
        .unwrap();
        let mut config: OperatorConfig =
            serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
        config.ai.protocol = "openai_compat".into();
        config.ai.base_url = "http://127.0.0.1:11434/v1".into();

        let snapshot = store
            .apply(ApplyOperatorConfig {
                config,
                secrets: BTreeMap::new(),
            })
            .unwrap();

        assert_eq!(snapshot["config"]["ai"]["default_model"], "alpha-model");
        assert_eq!(
            snapshot["config"]["ai"]["models"],
            json!(["alpha-model", "zeta-model"])
        );
        assert!(snapshot["config"]["ai"]["last_validated_at_unix_ms"].is_number());
        assert_eq!(snapshot["readiness"]["ai"], "ready");
    }

    #[test]
    fn applies_profile_with_vault_secret_and_renders_backend_environment() {
        let root = tempdir().unwrap();
        let store = OperatorConfigStore::load_with_vault(
            root.path().join("operator.json"),
            Arc::new(MemoryVault::default()),
        )
        .unwrap();
        let mut config: OperatorConfig =
            serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
        config.ai = AiProfile {
            protocol: "openai_compat".into(),
            base_url: "https://api.openai.com/v1".into(),
            default_model: "gpt-test".into(),
            models: vec!["gpt-test".into()],
            vision_models: vec![],
            ..Default::default()
        };

        store
            .apply(ApplyOperatorConfig {
                config,
                secrets: BTreeMap::from([("ai.api_key".into(), Some("secret-key".into()))]),
            })
            .unwrap();

        let environment = store.backend_environment().unwrap();
        assert_eq!(environment["LEMMA_OPENAI_API_KEY"], "secret-key");
        assert_eq!(environment["LEMMA_OPENAI_DEFAULT_MODEL"], "gpt-test");
        assert!(!fs::read_to_string(root.path().join("operator.json"))
            .unwrap()
            .contains("secret-key"));
    }

    #[test]
    fn loopback_openai_profile_uses_nonsecret_compatibility_sentinel() {
        let root = tempdir().unwrap();
        let store = OperatorConfigStore::load_with_vault(
            root.path().join("operator.json"),
            Arc::new(MemoryVault::default()),
        )
        .unwrap();
        let mut config: OperatorConfig =
            serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
        config.ai = AiProfile {
            protocol: "openai_compat".into(),
            base_url: "http://127.0.0.1:11434/v1".into(),
            default_model: "local".into(),
            models: vec!["local".into()],
            vision_models: vec![],
            ..Default::default()
        };

        store
            .apply(ApplyOperatorConfig {
                config,
                secrets: BTreeMap::new(),
            })
            .unwrap();

        assert_eq!(
            store.backend_environment().unwrap()["LEMMA_OPENAI_API_KEY"],
            "lemma-local"
        );
    }

    #[test]
    fn rejects_unknown_secrets_and_incomplete_surface_modes() {
        let root = tempdir().unwrap();
        let store = OperatorConfigStore::load_with_vault(
            root.path().join("operator.json"),
            Arc::new(MemoryVault::default()),
        )
        .unwrap();
        let mut config: OperatorConfig =
            serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
        config.surfaces.telegram_polling = true;

        assert!(store
            .apply(ApplyOperatorConfig {
                config: config.clone(),
                secrets: BTreeMap::new(),
            })
            .is_err());
        assert!(store
            .apply(ApplyOperatorConfig {
                config,
                secrets: BTreeMap::from([("surfaces.unknown".into(), Some("secret".into()))]),
            })
            .is_err());
    }

    #[test]
    fn restores_committed_config_and_every_vault_secret() {
        let root = tempdir().unwrap();
        let store = OperatorConfigStore::load_with_vault(
            root.path().join("operator.json"),
            Arc::new(MemoryVault::default()),
        )
        .unwrap();
        let mut config: OperatorConfig =
            serde_json::from_value(store.snapshot().unwrap()["config"].clone()).unwrap();
        config.ai = AiProfile {
            protocol: "openai_compat".into(),
            base_url: "https://models.example.test/v1".into(),
            default_model: "stable-model".into(),
            models: vec!["stable-model".into()],
            vision_models: vec![],
            ..Default::default()
        };
        store
            .apply(ApplyOperatorConfig {
                config,
                secrets: BTreeMap::from([("ai.api_key".into(), Some("old-secret".into()))]),
            })
            .unwrap();
        let old_snapshot = store.snapshot().unwrap();
        let old_state = store.capture_state().unwrap();

        let mut replacement: OperatorConfig =
            serde_json::from_value(old_snapshot["config"].clone()).unwrap();
        replacement.ai.default_model = "replacement-model".into();
        replacement.ai.models = vec!["replacement-model".into()];
        store
            .apply(ApplyOperatorConfig {
                config: replacement,
                secrets: BTreeMap::from([("ai.api_key".into(), Some("new-secret".into()))]),
            })
            .unwrap();

        let restored = store.restore_state(old_state).unwrap();
        assert_eq!(restored["config"], old_snapshot["config"]);
        assert_eq!(
            store.backend_environment().unwrap()["LEMMA_OPENAI_API_KEY"],
            "old-secret"
        );
        assert!(!fs::read_to_string(root.path().join("operator.json"))
            .unwrap()
            .contains("old-secret"));
    }
}
