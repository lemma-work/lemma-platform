//! What a caller may ask for: the shapes a request is parsed into.

use super::*;

#[derive(Clone, Debug, Deserialize)]
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
