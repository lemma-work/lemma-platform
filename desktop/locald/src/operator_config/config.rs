//! The operator config itself: what is in it, and reading one written by
//! an older build.

use super::*;

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
    /// The OAuth app a GitHub connector authenticates through.
    ///
    /// Named for the environment the backend reads rather than the vendor,
    /// because that name is declared by the connector catalog itself
    /// (scripts/lemma_apps_config.json) and not by anything here.
    #[serde(default)]
    pub github_client_id: String,
    /// Slack's connector OAuth app, which is not the same thing as the Slack
    /// *surface* below: a surface talks to one workspace with a bot token, a
    /// connector connects each user's own account.
    #[serde(default)]
    pub slack_client_id: String,
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

#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum OperatorConfigUpdate {
    Section(SectionUpdate),
    Legacy(Box<ApplyOperatorConfig>),
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SectionUpdate {
    pub expected_revision: u64,
    pub section: ConfigSection,
    #[serde(default)]
    pub secrets: BTreeMap<String, CredentialAction>,
}

#[derive(Debug, Deserialize)]
#[serde(
    tag = "name",
    content = "value",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum ConfigSection {
    Ai(AiProfile),
    Integrations(IntegrationConfig),
    Surfaces(SurfaceConfig),
}

#[derive(Debug, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum CredentialAction {
    Keep,
    Replace { value: String },
    Remove,
}

impl OperatorConfig {
    pub(crate) fn fresh() -> io::Result<Self> {
        Self::fresh_with_install_id(random_hex(16)?)
    }

    /// A default configuration that keeps an existing installation's identity.
    ///
    /// The identity is not decoration: every one of `SECRET_NAMES` is stored in
    /// the OS credential vault under `{install_id}:{name}`. Minting a new id
    /// while healing a broken config would leave all of them in the user's
    /// keychain, addressable by nothing -- `keyring` needs the account name to
    /// find an entry, so they could not even be enumerated to clean up.
    pub(crate) fn fresh_with_install_id(install_id: String) -> io::Result<Self> {
        Ok(Self {
            schema_version: CONFIG_SCHEMA_VERSION,
            install_id,
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

/// The oldest `schema_version` this build knows how to bring forward.
///
/// Anything below it, and anything above `CONFIG_SCHEMA_VERSION`, is quarantined
/// rather than guessed at.
pub(crate) const MIN_MIGRATABLE_CONFIG_SCHEMA_VERSION: u64 = 1;

/// Recover an installation identity from a config file that may be unreadable.
///
/// Exposed for `lemma-locald reset`, which must find the id *before* it deletes
/// the file naming it -- and which runs precisely when that file may be the
/// thing that is broken.
pub fn recover_install_id(path: &Path) -> Option<String> {
    salvage_install_id(&fs::read(path).ok()?)
}

/// Bring a stored configuration up to the current schema, or refuse it.
///
/// Operates on a `Value` rather than the typed struct on purpose: every nested
/// type carries `deny_unknown_fields`, so a strict parse is exactly what cannot
/// read a document written by a build that added a field. Working untyped first
/// is what lets a future v1 -> v2 bump drop unknown keys and fill in defaults
/// instead of bricking the install.
///
/// `sharing.json` has done this all along -- this is the same shape.
pub(crate) fn migrate_config(mut value: Value) -> Option<OperatorConfig> {
    let version = value.get("schema_version").and_then(Value::as_u64)?;
    if !(MIN_MIGRATABLE_CONFIG_SCHEMA_VERSION..=CONFIG_SCHEMA_VERSION).contains(&version) {
        // A newer schema is never downgraded. Quarantining it instead means
        // downgrade-then-upgrade gets the file back.
        return None;
    }

    // Future per-version fixups land here, oldest first. There is only one
    // schema today, so the sole job right now is to drop keys a newer build
    // wrote that this one does not know -- which is the case that made a
    // rollback fatal.
    let known: &[&str] = &[
        "schema_version",
        "install_id",
        "revision",
        "onboarding_complete",
        "ai",
        "integrations",
        "surfaces",
    ];
    if let Some(object) = value.as_object_mut() {
        object.retain(|key, _| known.contains(&key.as_str()));
    }
    value["schema_version"] = Value::from(CONFIG_SCHEMA_VERSION);

    serde_json::from_value(value).ok()
}

/// Recover just the installation identity from a file nothing else can read.
///
/// Deliberately the most permissive parse in this module: it runs when the
/// strict one has already failed, and what it protects is the link between this
/// installation and the 19 secrets in the user's keychain.
pub(crate) fn salvage_install_id(raw: &[u8]) -> Option<String> {
    fn looks_like_an_id(candidate: &str) -> bool {
        candidate.len() == 32 && candidate.bytes().all(|byte| byte.is_ascii_hexdigit())
    }

    // The structured read first, for a file that parses but does not fit.
    if let Ok(value) = serde_json::from_slice::<Value>(raw) {
        if let Some(install_id) = value.get("install_id").and_then(Value::as_str) {
            if looks_like_an_id(install_id) {
                return Some(install_id.to_owned());
            }
        }
    }

    // Then a plain text scan, because the case this exists for is a file that
    // is not JSON at all -- a truncated write, a torn page. A JSON parser
    // cannot help there, and the identity is a fixed-width hex string that is
    // trivially recognisable without one.
    let text = std::str::from_utf8(raw).ok()?;
    let after_key = text.split("\"install_id\"").nth(1)?;
    let opening = after_key.find('"')?;
    let rest = &after_key[opening + 1..];
    let closing = rest.find('"')?;
    let candidate = &rest[..closing];
    looks_like_an_id(candidate).then(|| candidate.to_owned())
}

pub(crate) fn configuration_schema() -> Value {
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
