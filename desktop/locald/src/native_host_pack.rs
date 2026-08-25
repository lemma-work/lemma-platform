//! Native renderer for the packaged two-process managed-local runtime.
//!
//! A signed Desktop release must work on a machine without a source checkout,
//! Python package manager, or `lemma-stack` executable. Compatibility providers
//! may still use the legacy renderer, but the app-owned VZ/WSL runtime is
//! rendered entirely by the durable daemon.

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::URL_SAFE;
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::network::{load_or_allocate, NetworkPorts};
use crate::paths::LocalPaths;

const POSTGRES_PORT: u16 = 55432;
const REDIS_PORT: u16 = 56379;
const SUPERTOKENS_PORT: u16 = 53567;
const LOCAL_FRONTEND_HOST: &str = "app.lemma.localhost";
// Safari/WKWebView blocks a response from a different hostname from setting
// the SuperTokens session cookies used by the top-level frontend. Keep the
// processes on separate ports, but expose both through one browser hostname.
// The backend still binds only to loopback and sandboxes use the explicit
// host.lemma.internal callback URLs below.
const LOCAL_BACKEND_HOST: &str = LOCAL_FRONTEND_HOST;
const LOCAL_CORS_ORIGIN_REGEX: &str = r"^https?://([a-z0-9-]+\.)*lemma\.localhost(:\d+)?$";

#[derive(Clone, Debug)]
pub(crate) struct ManagedManifestMaterial {
    pub postgres_password: String,
    pub redis_password: String,
    pub bridge_executable: PathBuf,
}

/// The per-installation seed every derived local key hangs off.
///
/// Never regenerate this for an existing installation: it also derives the key
/// that encrypts stored secrets, so a fresh one leaves every encrypted row
/// unreadable. `deny_unknown_fields` is deliberate — a field this does not
/// recognise means the file was written by something else.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct HostSecrets {
    installation_secret: String,
}

/// Where the code a manifest points at actually lives.
///
/// A released pack ships its own interpreter, its own Node, and a built Next
/// server. A developer's checkout has none of those and uses `uv` and
/// `next dev`. Those are the only differences — ports, environment, the
/// managed-runtime block, health checks and restart policy are identical — so
/// they are rendered once from here instead of forking the renderer. A dev run
/// that exercised a different supervisor would prove nothing about this one.
struct Bindings {
    /// Argv prefix that runs the backend's Python.
    python: Vec<String>,
    backend_dir: PathBuf,
    frontend_command: Vec<String>,
    frontend_dir: PathBuf,
    /// Assets that are baked into a pack but live in sibling projects in a
    /// checkout.
    browser_sdk: PathBuf,
    browser_ui: PathBuf,
    skills: PathBuf,
    /// `next dev` must not be told it is a production build.
    node_env: &'static str,
    /// Where the backend keeps the key that encrypts stored secrets.
    ///
    /// A packaged install holds real provider credentials and belongs in the
    /// OS keychain. A checkout cannot use it: the backend is a `uv run` child
    /// of locald with no GUI session, so macOS answers with a "keychain cannot
    /// be found" dialog and the run stalls before anything works. Source mode
    /// uses an in-config key derived from this installation's own secret, which
    /// is throwaway anyway — the whole dev root is under /tmp.
    secret_key_provider: &'static str,
}

/// A checkout to run instead of a released pack, and the release whose pinned
/// infrastructure images it should run against.
///
/// Selected by `LEMMA_LOCALD_SOURCE_ROOT`; set only by
/// `desktop/scripts/dev-local.sh --source`. A packaged app never sets it, and
/// the renderer behaves exactly as before when it is absent.
struct SourceLayout {
    root: PathBuf,
    release_manifest: PathBuf,
}

fn source_layout() -> io::Result<Option<SourceLayout>> {
    let Some(root) = std::env::var_os("LEMMA_LOCALD_SOURCE_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
    else {
        return Ok(None);
    };
    let release_manifest = std::env::var_os("LEMMA_LOCALD_SOURCE_RELEASE_MANIFEST")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| {
            invalid(
                "LEMMA_LOCALD_SOURCE_ROOT needs LEMMA_LOCALD_SOURCE_RELEASE_MANIFEST: a \
                 checkout has no release.json, but its infrastructure and sandbox images \
                 are still pinned ones",
            )
        })?;
    Ok(Some(SourceLayout {
        root,
        release_manifest,
    }))
}

pub(crate) fn prepare(
    paths: &LocalPaths,
    pack_root: &Path,
    material: ManagedManifestMaterial,
    healed: &mut Vec<String>,
) -> io::Result<PathBuf> {
    crate::host_process::reclaim_persisted_installation_processes(&paths.root)?;
    let ports = load_or_allocate(paths)?;
    let source = source_layout()?;
    let manifest = build(paths, pack_root, &material, ports, source.as_ref(), healed)?;
    let destination = paths.root.join("host-pack.json");
    write_private_atomic(&destination, &serde_json::to_vec_pretty(&manifest)?)?;
    Ok(destination)
}

fn packaged_bindings(root: &Path) -> io::Result<Bindings> {
    let python = required_file(
        root,
        "backend Python",
        &[
            "backend/python/bin/python3",
            "backend/python/bin/python",
            "backend/python/python.exe",
        ],
    )?;
    let node = required_file(
        root,
        "frontend Node.js",
        &["frontend/node/bin/node", "frontend/node/node.exe"],
    )?;
    let frontend_launcher = required_file(
        root,
        "frontend launcher",
        &["frontend/frontend-launcher.mjs"],
    )?;
    let frontend_server = required_file(
        root,
        "Next.js standalone server",
        &[
            "frontend/server.js",
            "frontend/app/server.js",
            "frontend/lemma-frontend/server.js",
        ],
    )?;
    let backend_dir = root.join("backend");
    Ok(Bindings {
        python: vec![path_text(&python)?],
        frontend_command: vec![
            path_text(&node)?,
            path_text(&frontend_launcher)?,
            path_text(&frontend_server)?,
        ],
        browser_sdk: backend_dir.join("assets/browser-sdk/lemma-client.js"),
        browser_ui: backend_dir.join("assets/browser-sdk/lemma-ui.js"),
        skills: backend_dir.join("assets/lemma-skills"),
        backend_dir,
        frontend_dir: root.join("frontend"),
        node_env: "production",
        secret_key_provider: "keychain",
    })
}

fn source_bindings(root: &Path) -> io::Result<Bindings> {
    source_bindings_with(root, &resolve_on_path("uv")?, &resolve_on_path("node")?)
}

/// The layout half of source mode, with the toolchain already located.
///
/// Split out so what a checkout *is* can be described without a machine that
/// has `uv` and `node` installed — a CI runner has neither, and a test about
/// where secrets live should not need them.
fn source_bindings_with(root: &Path, uv: &Path, node: &Path) -> io::Result<Bindings> {
    let backend_dir = required_dir(root, "the backend project", "lemma-backend")?;
    let frontend_dir = required_dir(root, "the frontend project", "lemma-frontend")?;
    // The backend owns sandbox provisioning, so one interpreter runs every
    // migration; only the working directory and config name differ.
    let launcher = required_file(
        root,
        "frontend launcher",
        &["desktop/runtime/frontend-launcher.mjs"],
    )?;
    Ok(Bindings {
        // `uv` and `node` come from PATH, which is the point: there is nothing
        // to stage before local mode runs the code being edited. Resolved to
        // absolute paths so locald can identify the processes it spawns.
        python: vec![
            path_text(uv)?,
            "run".to_owned(),
            "--project".to_owned(),
            path_text(&backend_dir)?,
            "python".to_owned(),
        ],
        frontend_command: vec![
            path_text(node)?,
            path_text(&launcher)?,
            "--dev".to_owned(),
            path_text(&frontend_dir)?,
        ],
        browser_sdk: root.join("lemma-typescript/public/lemma-client.js"),
        browser_ui: root.join("lemma-typescript/public/lemma-ui.js"),
        skills: root.join("lemma-skills"),
        backend_dir,
        frontend_dir,
        node_env: "development",
        secret_key_provider: "static",
    })
}

fn build(
    paths: &LocalPaths,
    pack_root: &Path,
    material: &ManagedManifestMaterial,
    ports: NetworkPorts,
    source: Option<&SourceLayout>,
    healed: &mut Vec<String>,
) -> io::Result<Value> {
    validate_hex_secret("postgres password", &material.postgres_password)?;
    validate_hex_secret("Redis password", &material.redis_password)?;

    // A checkout carries no release.json or pack.json of its own: its app code
    // is local, but the infrastructure and sandbox images it runs against are
    // still the pinned ones, borrowed from an installed release.
    let (bindings, release_path) = match source {
        Some(layout) => {
            let root = layout.root.canonicalize().map_err(|error| {
                io::Error::new(
                    error.kind(),
                    format!(
                        "source root is unavailable at {}: {error}",
                        layout.root.display()
                    ),
                )
            })?;
            (source_bindings(&root)?, layout.release_manifest.clone())
        }
        None => {
            let root = pack_root.canonicalize().map_err(|error| {
                io::Error::new(
                    error.kind(),
                    format!(
                        "native host pack is unavailable at {}: {error}",
                        pack_root.display()
                    ),
                )
            })?;
            if !root.is_dir() {
                return Err(invalid("native host pack root is not a directory"));
            }
            (packaged_bindings(&root)?, root.join("release.json"))
        }
    };
    let backend_dir = bindings.backend_dir.clone();
    let frontend_dir = bindings.frontend_dir.clone();

    let release: Value = read_json(&release_path, "native release manifest")?;
    let release_version = required_string(&release, "version", "native release manifest")?;
    if source.is_none() {
        let pack: Value = read_json(&pack_root.join("pack.json"), "native host pack marker")?;
        if pack.get("release").and_then(Value::as_str) != Some(release_version.as_str()) {
            return Err(invalid(
                "native host pack marker does not match its release",
            ));
        }
    }
    let workspace_image = pull_ref(
        release.pointer("/images/workspace"),
        "workspace sandbox image",
    )?;
    let function_image = pull_ref(
        release.pointer("/images/function"),
        "function sandbox image",
    )?;
    let postgres_image = pull_ref(release.pointer("/infra/postgres"), "Postgres image")?;
    let redis_image = pull_ref(release.pointer("/infra/redis"), "Redis image")?;
    let supertokens_image = pull_ref(release.pointer("/infra/supertokens"), "SuperTokens image")?;
    for (label, image) in [
        ("workspace sandbox", &workspace_image),
        ("function sandbox", &function_image),
        ("Postgres", &postgres_image),
        ("Redis", &redis_image),
        ("SuperTokens", &supertokens_image),
    ] {
        if !image.contains("@sha256:") {
            return Err(invalid(format!(
                "managed {label} image must be pinned by digest in release.json"
            )));
        }
    }

    let data_root = paths.root.join("data");
    let object_storage = data_root.join("object-storage");
    let files = data_root.join("files");
    let workspaces = data_root.join("workspaces");
    let state = paths.root.join("state");
    let process_home = state.join("home");
    let process_cache = state.join("cache");
    let process_config = state.join("config");
    let process_data = state.join("data");
    let tldextract_cache = process_cache.join("tldextract");
    let embedding_cache = process_cache.join("fastembed");
    for directory in [
        &object_storage,
        &files,
        &workspaces,
        &state,
        &state.join("emails"),
        &process_home,
        &process_cache,
        &process_config,
        &process_data,
        &tldextract_cache,
        &embedding_cache,
    ] {
        fs::create_dir_all(directory)?;
    }

    let secrets = load_or_create_host_secrets(&paths.root.join("host.secrets.json"), healed)?;
    // Derived from this installation's own secret rather than stored separately,
    // the same way the runtime credential key below is: stable across restarts
    // so encrypted rows stay readable, distinct from every other key by its
    // domain string, and gone when the data directory is. SHA-256 into url-safe
    // base64 is exactly a Fernet key. Only source mode uses it; see
    // `Bindings::secret_key_provider`.
    let secret_encryption_key = URL_SAFE.encode(Sha256::digest(
        [
            secrets.installation_secret.as_bytes(),
            b"lemma-secret-encryption-v1",
        ]
        .concat(),
    ));
    let runtime_key = URL_SAFE.encode(Sha256::digest(
        [
            secrets.installation_secret.as_bytes(),
            b"lemma-workspace-runtime-credential-v1",
        ]
        .concat(),
    ));
    let frontend_port = ports.frontend_port;
    let backend_port = ports.backend_port;
    let runtime_instance_id = random_hex(16)?;
    let frontend_origin = format!("http://{LOCAL_FRONTEND_HOST}:{frontend_port}");
    let backend_origin = format!("http://{LOCAL_BACKEND_HOST}:{backend_port}");
    let mut backend_env = BTreeMap::from([
        ("ENVIRONMENT", "local".to_owned()),
        ("DEBUG", "true".to_owned()),
        ("LOG_LEVEL", "INFO".to_owned()),
        ("JSON_LOGS_ENABLED", "true".to_owned()),
        ("LOCAL_HTTP_ACCESS_LOGS_ENABLED", "true".to_owned()),
        ("OBSERVABILITY_ENABLED", "false".to_owned()),
        // A packaged service must neither depend on nor mutate arbitrary user
        // home/cache state. Keep all library state app-owned on macOS and
        // Windows, and force SuperTokens to use tldextract's bundled PSL so
        // first startup also works offline.
        ("HOME", path_text(&process_home)?),
        ("XDG_CACHE_HOME", path_text(&process_cache)?),
        ("XDG_CONFIG_HOME", path_text(&process_config)?),
        ("XDG_DATA_HOME", path_text(&process_data)?),
        ("TLDEXTRACT_CACHE", path_text(&tldextract_cache)?),
        ("SUPERTOKENS_TLDEXTRACT_DISABLE_HTTP", "1".to_owned()),
        ("LOCAL_EMBEDDING_CACHE_DIR", path_text(&embedding_cache)?),
        // Desktop stores backend encryption material in the signed-in user's
        // OS vault. Never fall back to the deterministic local-development key
        // for a packaged installation.
        (
            "SECRET_KEY_PROVIDER",
            bindings.secret_key_provider.to_owned(),
        ),
        (
            "DATABASE_URL",
            format!(
                "postgresql+asyncpg://postgres:{}@127.0.0.1:{POSTGRES_PORT}/lemma",
                material.postgres_password
            ),
        ),
        (
            "DATASTORE_DATABASE_URL",
            format!(
                "postgresql+asyncpg://postgres:{}@127.0.0.1:{POSTGRES_PORT}/lemma_datastore",
                material.postgres_password
            ),
        ),
        (
            "REDIS_URL",
            format!(
                "redis://:{}@127.0.0.1:{REDIS_PORT}",
                material.redis_password
            ),
        ),
        (
            "SUPERTOKENS_CORE_URL",
            format!("http://127.0.0.1:{SUPERTOKENS_PORT}"),
        ),
        ("LOCAL_KREUZBERG_ENABLED", "false".to_owned()),
        ("KREUZBERG_URL", String::new()),
        ("DOCUMENT_PROCESSOR", "xberg".to_owned()),
        // One document at a time. The backend embeds every worker lane in the
        // API process, so bulk extraction shares a core count with the thing
        // the user is waiting on; the default of two, sized for a worker with a
        // container to itself, is felt here as UI latency.
        ("WORKER_BULK_CONCURRENCY", "1".to_owned()),
        // Sandboxes are provisioned in-process by the workspace module, so
        // there is no manager URL, key or database of its own here.
        ("WORKSPACE_PROVIDER", "lemma_local".to_owned()),
        ("WORKSPACE_RUNTIME_CREDENTIAL_KEY", runtime_key),
        ("WORKSPACE_IMAGE", workspace_image.clone()),
        ("FUNCTION_IMAGE", function_image.clone()),
        ("WORKSPACE_ADD_HOST_GATEWAY", "false".to_owned()),
        ("WORKSPACE_HOST_ALIAS", "host.lemma.internal".to_owned()),
        ("WORKSPACE_LOCAL_CALLBACK_REQUIRED", "true".to_owned()),
        (
            "WORKSPACE_LOCAL_CALLBACK_URL",
            format!("http://host.lemma.internal:{backend_port}"),
        ),
        (
            "WORKSPACE_LOCAL_RUNTIME_CLI",
            path_text(&material.bridge_executable)?,
        ),
        (
            "WORKSPACE_CALLBACK_API_URL",
            format!("http://host.lemma.internal:{backend_port}"),
        ),
        (
            "WORKSPACE_CALLBACK_AUTH_URL",
            format!("http://host.lemma.internal:{frontend_port}/auth"),
        ),
        (
            "WORKSPACE_CALLBACK_FRONTEND_URL",
            format!("http://host.lemma.internal:{frontend_port}"),
        ),
        ("API_URL", backend_origin.clone()),
        ("FRONTEND_URL", frontend_origin.clone()),
        ("AUTH_FRONTEND_URL", format!("{frontend_origin}/auth")),
        (
            "SCHEDULER_API_URL",
            format!("http://127.0.0.1:{backend_port}"),
        ),
        ("AUTH_WEBSITE_BASE_PATH", "/auth".to_owned()),
        ("SUPERTOKENS_API_BASE_PATH", "/auth".to_owned()),
        ("SUPERTOKENS_API_GATEWAY_PATH", "/st".to_owned()),
        ("SESSION_COOKIE_SECURE", "false".to_owned()),
        ("SESSION_COOKIE_SAME_SITE", "lax".to_owned()),
        // Keep local auth cookies host-only on app.lemma.localhost. The
        // frontend and API use this one hostname on separate ports so
        // Safari/WKWebView accepts the HttpOnly cookies without widening them
        // to user-authored app subdomains.
        ("SESSION_COOKIE_DOMAIN", String::new()),
        (
            "APP_BASE_DOMAIN",
            format!("apps.lemma.localhost:{backend_port}"),
        ),
        ("CORS_ORIGIN_REGEX", LOCAL_CORS_ORIGIN_REGEX.to_owned()),
        ("STORAGE_BACKEND", "local".to_owned()),
        ("LOCAL_OBJECT_STORAGE_ROOT", path_text(&object_storage)?),
        ("LOCAL_FILE_STORAGE_ROOT", path_text(&files)?),
        (
            "LOCAL_AGENT_RUNTIME_CONFIG_PATH",
            path_text(&state.join("agent-runtime.json"))?,
        ),
        ("EMAIL_TRANSPORT", "filesystem".to_owned()),
        ("EMAIL_OUTPUT_DIR", path_text(&state.join("emails"))?),
        ("AUTH_EMAIL_VERIFICATION_REQUIRED", "false".to_owned()),
        (
            "AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED",
            "false".to_owned(),
        ),
        ("AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED", "false".to_owned()),
        ("AUTH_ABUSE_PROTECTION_ENABLED", "false".to_owned()),
        ("AUTH_ALTCHA_ENABLED", "false".to_owned()),
        ("DESKTOP_AUTH_CREATE_LIMIT", "0".to_owned()),
        (
            "AUTH_WHATSAPP_MOBILE_VERIFICATION_ENABLED",
            "false".to_owned(),
        ),
        ("EMBEDDING_PROVIDER", "local".to_owned()),
        ("LOCAL_EMBEDDING_STARTUP_MODE", "background".to_owned()),
        ("LEMMA_RUNTIME_INSTANCE_ID", runtime_instance_id.clone()),
        ("LEMMA_LOCALD_PARENT_WATCHDOG", "1".to_owned()),
        ("WEB_SEARCH_PROVIDER", "duckduckgo".to_owned()),
        ("ENABLE_TELEGRAM_POLLING_MODE", "true".to_owned()),
        ("ENABLE_SLACK_SOCKET_MODE", "true".to_owned()),
        // The desktop app has no public webhook, so inbound Resend email is
        // pulled by the worker's polling receiver instead. This also lets a
        // Resend surface be provisioned against the localhost API URL, so
        // message_user/notifications work once a key + inbound domain are set.
        ("ENABLE_RESEND_POLLING_MODE", "true".to_owned()),
    ]);
    if bindings.secret_key_provider == "static" {
        backend_env.insert("SECRET_ENCRYPTION_KEY", secret_encryption_key);
    }
    let browser_sdk = &bindings.browser_sdk;
    let browser_ui = &bindings.browser_ui;
    let skills = &bindings.skills;
    if browser_sdk.is_file() {
        backend_env.insert("BROWSER_SDK_PATH", path_text(browser_sdk)?);
    }
    if browser_ui.is_file() {
        backend_env.insert("BROWSER_UI_PATH", path_text(browser_ui)?);
    }
    if skills.is_dir() {
        backend_env.insert("LEMMA_SKILLS_ROOT", path_text(skills)?);
    }

    let frontend_env = BTreeMap::from([
        ("NODE_ENV", bindings.node_env.to_owned()),
        ("PORT", frontend_port.to_string()),
        ("HOSTNAME", "127.0.0.1".to_owned()),
        ("NEXT_PUBLIC_API_URL", backend_origin),
        ("NEXT_PUBLIC_AUTH_URL", format!("{frontend_origin}/auth")),
        ("NEXT_PUBLIC_SITE_URL", frontend_origin.clone()),
        ("NEXT_PUBLIC_AUTH_WEBSITE_BASE_PATH", "/auth".to_owned()),
        ("NEXT_PUBLIC_SUPERTOKENS_API_BASE_PATH", "/auth".to_owned()),
        ("NEXT_PUBLIC_SUPERTOKENS_API_GATEWAY_PATH", "/st".to_owned()),
        (
            "NEXT_PUBLIC_AUTH_DEFAULT_REDIRECT_URI",
            format!("{frontend_origin}/"),
        ),
        ("NEXT_PUBLIC_SESSION_TOKEN_DOMAIN", String::new()),
        (
            "NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED",
            "false".to_owned(),
        ),
        (
            "NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID",
            runtime_instance_id.clone(),
        ),
        // Marks the deployment, not the client. The desktop webview announces
        // itself with a `__LEMMA_DESKTOP__` global, but a phone on the same
        // Wi-Fi or someone holding a public link has no such global and is
        // still looking at a local install — this is what tells them apart from
        // hosted Lemma, and it is what suppresses the marketing landing page
        // for all three.
        ("NEXT_PUBLIC_LEMMA_DEPLOYMENT", "local".to_owned()),
        ("LEMMA_LOCALD_PARENT_WATCHDOG", "1".to_owned()),
    ]);

    Ok(json!({
        "schema_version": 1,
        "release": release_version,
        "managed_runtime": {
            "images": {
                "postgres": postgres_image,
                "redis": redis_image,
                "supertokens": supertokens_image,
                // Carried so start can warm them. They are only *used* by a
                // sandbox, but pulling them the first time one is asked for
                // stopped a pod mid-task with no progress and no explanation.
                "workspace": workspace_image,
                "function": function_image,
            },
            "credentials": {
                "postgres_password": material.postgres_password,
                "redis_password": material.redis_password,
            },
            "ports": {
                "postgres": POSTGRES_PORT,
                "redis": REDIS_PORT,
                "supertokens": SUPERTOKENS_PORT,
                "backend": backend_port,
                "frontend": frontend_port,
            },
        },
        "setup": [
            {
                "id": "migrations",
                "command": argv(&bindings.python, &["-m", "alembic", "-c", "alembic.ini", "upgrade", "head"]),
                "cwd": path_text(&backend_dir)?,
                "env": backend_env.clone(),
                "timeout_seconds": 300,
                "max_attempts": 5,
                "retry_backoff_seconds": 3,
            },
            // Seeds the connector catalog. Without it a packaged install has no
            // connectors at all: `make dev` seeds one and the shipped app never
            // did, so this ran only on developer machines.
            //
            // No --provider flag on purpose. The importer always syncs the
            // native apps and adds the Composio ones only when
            // COMPOSIO_API_KEY is set, skipping them cleanly when it is not --
            // which is also what makes adding a key later work: the step runs
            // on every start, so the next one picks the Composio apps up
            // without anything else to remember.
            //
            // Optional, because it reaches the network when a key is set and a
            // workspace that will not start because a third-party catalog was
            // unreachable is a bad trade for a feature this session may not
            // even use. One attempt for the same reason: retrying a slow
            // import four more times would hold the whole start open.
            {
                "id": "connector-catalog",
                "command": argv(
                    &bindings.python,
                    &["scripts/import_connector_catalog.py"],
                ),
                "cwd": path_text(&backend_dir)?,
                "env": backend_env,
                "timeout_seconds": 600,
                "max_attempts": 1,
                "retry_backoff_seconds": 0,
                "optional": true,
            },
        ],
        "services": [
            {
                "id": "backend",
                "command": argv(&bindings.python, &["-m", "uvicorn", "local_app:app", "--host", "127.0.0.1", "--port", &backend_port.to_string(), "--ws", "websockets-sansio"]),
                "cwd": path_text(&backend_dir)?,
                "env": backend_env,
                "dependencies": [],
                "health": {
                    "url": format!("http://127.0.0.1:{backend_port}/health/ready"),
                    "timeout_seconds": 180,
                    "expected_body": runtime_instance_id,
                    "stabilization_seconds": 2
                },
                "restart": {"max_restarts": 3, "window_seconds": 60, "backoff_seconds": 2},
            },
            {
                "id": "frontend",
                "command": bindings.frontend_command.clone(),
                "cwd": path_text(&frontend_dir)?,
                "env": frontend_env,
                "dependencies": ["backend"],
                "health": {
                    "url": format!("http://127.0.0.1:{frontend_port}/runtime-config.js"),
                    "timeout_seconds": 120,
                    "expected_body": runtime_instance_id,
                    "stabilization_seconds": 2
                },
                "restart": {"max_restarts": 3, "window_seconds": 60, "backoff_seconds": 2},
            },
        ],
    }))
}

/// Join an interpreter prefix to the arguments that follow it.
///
/// A packaged pack's prefix is one absolute path; a checkout's is
/// `uv run --project <dir> python`. Callers should not have to care which.
fn argv(prefix: &[String], rest: &[&str]) -> Vec<String> {
    let mut command = prefix.to_vec();
    command.extend(rest.iter().map(|value| (*value).to_owned()));
    command
}

/// Resolve a PATH tool to an absolute path, the way a pack's own binaries are.
///
/// Not cosmetic. `record_child` identifies a spawned process with
/// `ps -o comm=`, which reports exactly the argv[0] it was launched with, and
/// then canonicalizes it. Spawned as bare `uv`, that is the string "uv" and the
/// canonicalize fails with ENOENT — so locald tears the process back down with
/// "could not record ownership of backend" and local mode never starts.
fn resolve_on_path(tool: &str) -> io::Result<PathBuf> {
    let path = std::env::var_os("PATH").ok_or_else(|| invalid("PATH is not set"))?;
    // On Windows the thing on PATH is uv.exe or node.exe -- never the bare
    // name -- so joining `tool` alone found neither and source mode could not
    // resolve its toolchain at all.
    #[cfg(windows)]
    let names: Vec<String> = ["", ".exe", ".cmd", ".bat"]
        .iter()
        .map(|suffix| format!("{tool}{suffix}"))
        .collect();
    #[cfg(not(windows))]
    let names: Vec<String> = vec![tool.to_owned()];

    std::env::split_paths(&path)
        .flat_map(|directory| {
            names
                .iter()
                .map(|name| directory.join(name))
                .collect::<Vec<_>>()
        })
        .find(|candidate| candidate.is_file())
        .ok_or_else(|| {
            invalid(format!(
                "source mode needs {tool} on PATH; it runs the checkout directly"
            ))
        })?
        .canonicalize()
}

fn required_dir(root: &Path, label: &str, relative: &str) -> io::Result<PathBuf> {
    let candidate = root.join(relative);
    if candidate.is_dir() {
        return candidate.canonicalize();
    }
    Err(invalid(format!(
        "source checkout is missing {label}: {}",
        candidate.display()
    )))
}

fn required_file(root: &Path, label: &str, candidates: &[&str]) -> io::Result<PathBuf> {
    for relative in candidates {
        let candidate = root.join(relative);
        if candidate.is_file() {
            return candidate.canonicalize();
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!(
            "native host pack is missing {label}; expected one of {}",
            candidates.join(", ")
        ),
    ))
}

fn read_json(path: &Path, label: &str) -> io::Result<Value> {
    serde_json::from_slice(&fs::read(path)?)
        .map_err(|error| invalid(format!("invalid {label}: {error}")))
}

fn required_string(value: &Value, key: &str, label: &str) -> io::Result<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .ok_or_else(|| invalid(format!("{label} has no {key}")))
}

fn pull_ref(value: Option<&Value>, label: &str) -> io::Result<String> {
    let reference = match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Object(value)) => {
            let base = value.get("ref").and_then(Value::as_str).unwrap_or_default();
            let digest = value.get("digest").and_then(Value::as_str);
            match digest {
                Some(digest) if !base.contains('@') => format!("{base}@{digest}"),
                _ => base.to_owned(),
            }
        }
        _ => String::new(),
    };
    if reference.is_empty() || reference.bytes().any(|byte| byte.is_ascii_whitespace()) {
        return Err(invalid(format!(
            "native release manifest has no valid {label}"
        )));
    }
    Ok(reference)
}

/// Read the installation secret, replacing it only if unreadable.
///
/// `installation_secret` derives the Fernet key for encrypted columns and the
/// workspace runtime credential key. Its invariant is that it is gone when the
/// data directory is -- so reminting it while the data directory survives makes
/// every encrypted row permanently undecryptable, quietly. Healing it therefore
/// also records that the data must be reset.
fn load_or_create_host_secrets(path: &Path, healed: &mut Vec<String>) -> io::Result<HostSecrets> {
    if path.is_file() {
        match read_existing_host_secrets(path) {
            Ok(secrets) => return Ok(secrets),
            Err(reason) => {
                let aside = crate::paths::quarantine_aside(path)?;
                if let Some(root) = path.parent() {
                    crate::paths::require_data_reset(
                        root,
                        "this installation's secret was replaced, and anything encrypted with \
                         the previous one can no longer be read",
                    )?;
                }
                healed.push(format!(
                    "the installation secret was unreadable ({reason}); kept as {} and replaced. \
                     Encrypted local data cannot be decrypted with the new one",
                    aside.display()
                ));
            }
        }
    }
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    let secrets = HostSecrets {
        installation_secret: bytes.iter().map(|byte| format!("{byte:02x}")).collect(),
    };
    write_private_atomic(path, &serde_json::to_vec(&secrets)?)?;
    Ok(secrets)
}

fn read_existing_host_secrets(path: &Path) -> Result<HostSecrets, String> {
    ensure_private_file(path).map_err(|error| error.to_string())?;
    let raw = fs::read(path).map_err(|error| error.to_string())?;
    let secrets: HostSecrets =
        serde_json::from_slice(&raw).map_err(|error| format!("invalid JSON: {error}"))?;
    validate_hex_secret("installation secret", &secrets.installation_secret)
        .map_err(|error| error.to_string())?;
    Ok(secrets)
}

fn random_hex(byte_count: usize) -> io::Result<String> {
    let mut bytes = vec![0_u8; byte_count];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn validate_hex_secret(label: &str, value: &str) -> io::Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(invalid(format!(
            "{label} is not a 32-byte lowercase hex secret"
        )));
    }
    Ok(())
}

fn path_text(path: &Path) -> io::Result<String> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        invalid(format!(
            "runtime path is not valid UTF-8: {}",
            path.display()
        ))
    })
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| invalid("private file has no parent directory"))?;
    fs::create_dir_all(parent)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = parent.join(format!(".native-host-{}-{nonce}.tmp", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    ensure_private_file(path)
}

fn ensure_private_file(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn fixture(root: &Path) {
        for relative in [
            "backend/python/bin/python3",
            "frontend/node/bin/node",
            "frontend/frontend-launcher.mjs",
            "frontend/app/server.js",
            "backend/assets/browser-sdk/lemma-client.js",
            "backend/assets/browser-sdk/lemma-ui.js",
        ] {
            let path = root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, b"fixture").unwrap();
        }
        fs::create_dir_all(root.join("backend/assets/lemma-skills")).unwrap();
        fs::write(
            root.join("pack.json"),
            br#"{"schema_version":1,"release":"6.2.0"}"#,
        )
        .unwrap();
        fs::write(
            root.join("release.json"),
            br#"{
              "schema_version": 1,
              "version": "6.2.0",
              "images": {
                "workspace": {
                  "ref": "workspace",
                  "digest": "sha256:workspace"
                },
                "function": {
                  "ref": "function",
                  "digest": "sha256:function"
                }
              },
              "infra": {
                "postgres": {"ref": "postgres", "digest": "sha256:postgres"},
                "redis": "redis@sha256:redis",
                "supertokens": "supertokens@sha256:supertokens"
              }
            }"#,
        )
        .unwrap();
    }

    #[test]
    fn renders_packaged_managed_runtime_without_compatibility_supervisor() {
        let root = tempdir().unwrap();
        let pack = root.path().join("pack");
        fs::create_dir_all(&pack).unwrap();
        fixture(&pack);
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        let output = prepare(
            &paths,
            &pack,
            ManagedManifestMaterial {
                postgres_password: "a".repeat(64),
                redis_password: "b".repeat(64),
                bridge_executable: PathBuf::from("/signed/lemma-runtime"),
            },
            &mut Vec::new(),
        )
        .unwrap();
        let manifest: Value = serde_json::from_slice(&fs::read(output).unwrap()).unwrap();
        let frontend_port = manifest["managed_runtime"]["ports"]["frontend"]
            .as_u64()
            .unwrap();
        let backend_port = manifest["managed_runtime"]["ports"]["backend"]
            .as_u64()
            .unwrap();
        let runtime_instance = manifest["services"][0]["env"]["LEMMA_RUNTIME_INSTANCE_ID"]
            .as_str()
            .unwrap();

        assert_eq!(manifest["release"], "6.2.0");
        assert_eq!(manifest["services"].as_array().unwrap().len(), 2);
        // One migration chain: the manager's own database is gone. The
        // connector catalog is seeded straight after it, because a packaged
        // install has no connectors at all until something does.
        assert_eq!(manifest["setup"].as_array().unwrap().len(), 2);
        assert_eq!(manifest["setup"][0]["id"], "migrations");
        assert_eq!(manifest["setup"][1]["id"], "connector-catalog");
        // Seeding reaches the network when a Composio key is set, and a
        // workspace must not fail to start because a third-party catalog was
        // unreachable.
        assert_eq!(manifest["setup"][1]["optional"], true);
        assert_ne!(manifest["setup"][0]["optional"], serde_json::json!(true));
        // No --provider flag: native always, Composio only when a key is set,
        // which is what lets adding a key later work on the next start.
        let catalog = manifest["setup"][1]["command"].as_array().unwrap();
        assert!(catalog.iter().all(|arg| arg.as_str() != Some("--provider")));
        assert_eq!(
            manifest["services"][0]["env"]["WORKSPACE_PROVIDER"],
            "lemma_local"
        );
        assert!(!manifest["services"][0]["command"]
            .as_array()
            .unwrap()
            .iter()
            .any(|argument| argument == "--no-access-log"));
        assert_eq!(
            manifest["services"][0]["env"]["WORKSPACE_CALLBACK_API_URL"],
            format!("http://host.lemma.internal:{backend_port}")
        );
        assert!(manifest["services"][0]["env"]
            .get("FUNCTION_RUNTIME_SECRET")
            .is_none());
        assert_eq!(
            manifest["services"][0]["env"]["DOCUMENT_PROCESSOR"],
            "xberg"
        );
        assert_eq!(
            manifest["services"][0]["env"]["HOME"],
            path_text(&paths.root.join("state").join("home")).unwrap()
        );
        assert_eq!(
            manifest["services"][0]["env"]["XDG_CACHE_HOME"],
            path_text(&paths.root.join("state").join("cache")).unwrap()
        );
        assert_eq!(
            manifest["services"][0]["env"]["TLDEXTRACT_CACHE"],
            path_text(&paths.root.join("state").join("cache").join("tldextract")).unwrap()
        );
        assert_eq!(
            manifest["services"][0]["env"]["SUPERTOKENS_TLDEXTRACT_DISABLE_HTTP"],
            "1"
        );
        assert_eq!(
            manifest["services"][0]["env"]["LOCAL_EMBEDDING_CACHE_DIR"],
            path_text(&paths.root.join("state").join("cache").join("fastembed")).unwrap()
        );
        assert_eq!(
            manifest["services"][0]["env"]["SECRET_KEY_PROVIDER"],
            "keychain"
        );
        // A packaged install must never carry the key in its own config; the
        // keychain is the point.
        assert!(manifest["services"][0]["env"]
            .get("SECRET_ENCRYPTION_KEY")
            .is_none());
        assert!(paths
            .root
            .join("state")
            .join("cache")
            .join("tldextract")
            .is_dir());
        assert!(paths
            .root
            .join("state")
            .join("cache")
            .join("fastembed")
            .is_dir());
        assert_eq!(
            manifest["services"][0]["env"]["AUTH_EMAIL_VERIFICATION_REQUIRED"],
            "false"
        );
        assert_eq!(
            manifest["services"][0]["env"]["LOCAL_HTTP_ACCESS_LOGS_ENABLED"],
            "true"
        );
        assert_eq!(
            manifest["services"][0]["env"]["AUTH_ABUSE_PROTECTION_ENABLED"],
            "false"
        );
        assert_eq!(
            manifest["services"][0]["env"]["DESKTOP_AUTH_CREATE_LIMIT"],
            "0"
        );
        assert_eq!(manifest["services"][0]["env"]["SESSION_COOKIE_DOMAIN"], "");
        assert_eq!(
            manifest["services"][0]["env"]["API_URL"],
            format!("http://app.lemma.localhost:{backend_port}")
        );
        assert_eq!(
            manifest["services"][1]["env"]["NEXT_PUBLIC_SESSION_TOKEN_DOMAIN"],
            ""
        );
        assert_eq!(
            manifest["services"][1]["env"]["NEXT_PUBLIC_API_URL"],
            format!("http://app.lemma.localhost:{backend_port}")
        );
        assert_eq!(
            manifest["services"][0]["env"]["WORKSPACE_LOCAL_CALLBACK_URL"],
            format!("http://host.lemma.internal:{backend_port}")
        );
        assert_eq!(
            manifest["services"][0]["env"]["WORKSPACE_IMAGE"],
            "workspace@sha256:workspace"
        );
        assert_eq!(
            manifest["services"][0]["env"]["FUNCTION_IMAGE"],
            "function@sha256:function"
        );
        assert_eq!(
            manifest["managed_runtime"]["images"]["postgres"],
            "postgres@sha256:postgres"
        );
        assert!(frontend_port >= 49_152);
        assert!(backend_port >= 49_152);
        assert_ne!(frontend_port, backend_port);
        assert_eq!(
            manifest["services"][0]["env"]["LOCAL_EMBEDDING_STARTUP_MODE"],
            "background"
        );
        assert_eq!(
            manifest["services"][1]["env"]["NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID"],
            runtime_instance
        );
        assert_eq!(
            manifest["services"][0]["health"]["expected_body"],
            runtime_instance
        );
        assert_eq!(
            manifest["services"][1]["health"]["expected_body"],
            runtime_instance
        );
        assert!(paths.root.join("host.secrets.json").is_file());
    }

    #[test]
    fn managed_infrastructure_images_must_be_digest_pinned() {
        let root = tempdir().unwrap();
        let pack = root.path().join("pack");
        fs::create_dir_all(&pack).unwrap();
        fixture(&pack);
        let mut release: Value = read_json(&pack.join("release.json"), "fixture").unwrap();
        release["infra"]["redis"] = Value::String("redis:latest".into());
        fs::write(
            pack.join("release.json"),
            serde_json::to_vec(&release).unwrap(),
        )
        .unwrap();
        let paths = LocalPaths::new(root.path().join("locald"));
        paths.ensure().unwrap();
        let error = build(
            &paths,
            &pack,
            &ManagedManifestMaterial {
                postgres_password: "a".repeat(64),
                redis_password: "b".repeat(64),
                bridge_executable: PathBuf::from("/signed/lemma-runtime"),
            },
            load_or_allocate(&paths).unwrap(),
            None,
            &mut Vec::new(),
        )
        .unwrap_err();
        assert!(error.to_string().contains("Redis image must be pinned"));
    }

    #[test]
    fn a_checkout_never_reaches_for_the_keychain() {
        // The source-mode backend is a `uv run` child of locald with no GUI
        // session. Asking macOS for the login keychain there does not fail
        // quietly — it puts up "a keychain cannot be found to store
        // secret-encryption-keyset" and the whole run stalls behind a dialog.
        let root = tempfile::tempdir().unwrap();
        for directory in ["lemma-backend", "lemma-frontend"] {
            fs::create_dir_all(root.path().join(directory)).unwrap();
        }
        fs::create_dir_all(root.path().join("desktop/runtime")).unwrap();
        fs::write(
            root.path().join("desktop/runtime/frontend-launcher.mjs"),
            "",
        )
        .unwrap();

        // Named rather than resolved: a CI runner has neither tool installed,
        // and where secrets live does not depend on them.
        let source = source_bindings_with(
            root.path(),
            Path::new("/usr/bin/uv"),
            Path::new("/usr/bin/node"),
        )
        .unwrap();
        assert_eq!(source.secret_key_provider, "static");

        let pack = tempfile::tempdir().unwrap();
        // A packaged install holds real provider credentials and must keep them
        // in the OS keychain, so the two must not converge.
        assert_ne!(
            source.secret_key_provider,
            packaged_bindings(pack.path())
                .map(|bindings| bindings.secret_key_provider)
                .unwrap_or("keychain")
        );
    }
}
