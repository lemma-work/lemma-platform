//! What a caller may ask for: the shapes a request is parsed into.

use super::*;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AppSpec {
    pub(crate) name: String,
    pub(crate) public_slug: String,
    pub(crate) port: u16,
    #[serde(default = "default_health_path")]
    pub(crate) health_path: String,
    #[serde(default)]
    pub(crate) startup: String,
    #[serde(default)]
    pub(crate) exposure: String,
    #[serde(default)]
    pub(crate) auth_mode: String,
}

pub(crate) fn default_health_path() -> String {
    "/health".into()
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ResourceSpec {
    pub(crate) memory: Option<String>,
    pub(crate) cpus: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CallbackSpec {
    #[serde(default)]
    pub(crate) required: bool,
    #[serde(default)]
    pub(crate) url: Option<String>,
    #[serde(default = "default_health_path")]
    pub(crate) health_path: String,
    #[serde(default = "default_callback_timeout")]
    pub(crate) timeout_seconds: f64,
}

pub(crate) fn default_callback_timeout() -> f64 {
    30.0
}

impl Default for CallbackSpec {
    fn default() -> Self {
        Self {
            required: false,
            url: None,
            health_path: default_health_path(),
            timeout_seconds: default_callback_timeout(),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum WorkloadKind {
    Workspace,
    Function,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct EnsureParameters {
    pub(crate) sandbox_id: String,
    pub(crate) workload_kind: WorkloadKind,
    pub(crate) image: String,
    #[serde(default)]
    pub(crate) env: BTreeMap<String, String>,
    #[serde(default)]
    pub(crate) metadata: BTreeMap<String, String>,
    #[serde(default)]
    pub(crate) runtime_token: Option<String>,
    pub(crate) apps: Vec<AppSpec>,
    #[serde(default)]
    pub(crate) resources: ResourceSpec,
    #[serde(default)]
    pub(crate) callback: CallbackSpec,
    /// Whether `host.lemma.internal` resolves inside the container.
    ///
    /// Defaulted to true so every caller that does not send it -- which is
    /// every caller today -- keeps the reach it has: the workspace runtime's
    /// callbacks to the backend and the function gateway both go through that
    /// name. It exists so a later change can withhold it from sandboxes that
    /// have no business reaching the Mac.
    ///
    /// A name, not a wall. Without the alias a container can still dial the
    /// host gateway by address; closing that needs a per-container firewall
    /// rule, and this flag is what such a rule would key on.
    #[serde(default = "default_host_access")]
    pub(crate) host_access: bool,
}

pub(crate) fn default_host_access() -> bool {
    true
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CoreImages {
    pub(crate) postgres: String,
    pub(crate) redis: String,
    pub(crate) supertokens: String,
    /// The sandbox images, warmed at start rather than on first use.
    ///
    /// Optional so a host pack that predates this still parses -- `deny_unknown_fields`
    /// is on the struct, not the absence of a field.
    #[serde(default)]
    pub(crate) workspace: Option<String>,
    #[serde(default)]
    pub(crate) function: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CoreCredentials {
    pub(crate) postgres_password: String,
    pub(crate) redis_password: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CoreParameters {
    pub(crate) images: CoreImages,
    pub(crate) credentials: CoreCredentials,
}

#[derive(Clone, Copy)]
pub(crate) enum CoreStage {
    Images,
    SandboxImages,
    Postgres,
    Redis,
    SuperTokens,
}

/// What a workspace sandbox serves, for a container that did not record it.
///
/// The list a sandbox is actually run with arrives in `sandbox.ensure` and is
/// written to `lemma.work/apps`, which is what `snapshot_from_inspect` reads.
/// This is the fallback for containers created before that label existed, and
/// it is the reason for the note: it is a *copy* of a list the backend owns
/// (`workspace/providers/lemma_local.py`), and a copy is how the two came to
/// disagree. The browser relay was declared there, published here, and missing
/// from this list -- so every snapshot omitted port 4850, `reach_port` refused
/// it, and the whole browser surface (the VNC pane, `browser_sign_in`, saved
/// logins) was unreachable on Desktop while the container was listening the
/// entire time. Anything added on the Python side belongs here too, until
/// every sandbox in the field carries the label and this can go.
pub(crate) fn workspace_apps() -> Vec<AppSpec> {
    vec![
        AppSpec {
            name: "runtime".into(),
            public_slug: "runtime".into(),
            port: 8080,
            health_path: "/health".into(),
            startup: "eager".into(),
            exposure: "private".into(),
            auth_mode: "manager_api_key".into(),
        },
        AppSpec {
            name: "browser".into(),
            public_slug: "browser".into(),
            port: 4848,
            health_path: "/health".into(),
            startup: "lazy".into(),
            exposure: "workspace_user".into(),
            auth_mode: "workspace_access_token".into(),
        },
        AppSpec {
            name: "relay".into(),
            public_slug: "relay".into(),
            port: 4850,
            health_path: "/health".into(),
            startup: "lazy".into(),
            // Private: only the backend dials this, holding the token it
            // delivered. The dashboard on 4848 is the one a person reaches.
            exposure: "private".into(),
            auth_mode: "manager_api_key".into(),
        },
    ]
}

pub(crate) fn function_apps() -> Vec<AppSpec> {
    vec![AppSpec {
        name: "function".into(),
        public_slug: "function".into(),
        port: 8090,
        health_path: "/healthz".into(),
        startup: "eager".into(),
        exposure: "private".into(),
        auth_mode: "manager_api_key".into(),
    }]
}
