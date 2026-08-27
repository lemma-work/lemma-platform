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
        // The same URL, for function sandboxes, which had none.
        //
        // `api_url` is `http://app.lemma.localhost:<port>`, and that name
        // resolves only on the Mac: `*.localhost` is a host resolver
        // convention, and a Linux container inside the VM has never heard of
        // it. The function dispatcher falls back to `api_url` when this is
        // unset, so every schema extraction and every function call reached
        // `getaddrinfo` and stopped there --
        //
        // ```text
        // ConnectError: [Errno -3] Temporary failure in name resolution
        // ```
        //
        // -- which is reported as `FUNCTION_VALIDATION_ERROR: Function schema
        // extraction failed`, and reads as a problem with the user's function
        // rather than with the address we handed the sandbox.
        //
        // `host.lemma.internal` is what guestd `--add-host`es into every
        // workload container, which is why the workspace line above works and
        // is exactly what this needs.
        (
            "FUNCTION_RUNTIME_GATEWAY_URL",
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
        // Wide enough to cover the app subdomains, which is what makes a pod
        // app a signed-in page instead of a 401.
        //
        // This used to be empty, keeping the cookie host-only on
        // app.lemma.localhost, on the theory that Safari/WKWebView would then
        // accept it without exposing it to user-authored app subdomains. The
        // second half of that was true and the first half was not: `localhost`
        // is not in the Public Suffix List, so WebKit cannot derive a
        // registrable domain and treats `<slug>.apps.lemma.localhost` as a
        // *different site* from `app.lemma.localhost`. Every request an app
        // made to the API was third-party, ITP dropped the cookie, and every
        // pod app loaded unauthenticated. Chromium sends it, and on lemma.work
        // the two hosts really are same-site -- which is why this reproduced
        // only in the shipping desktop app.
        //
        // Widening alone does not fix it: a cross-origin request from an app
        // host is blocked whatever the cookie's Domain and SameSite say (all
        // three combinations measured). It works because `build_runtime_config`
        // points an app's SDK at its *own* origin, making those calls
        // first-party, where this Domain is what puts the cookie in scope.
        // Both halves are required; neither is sufficient.
        //
        // What that exposes: the cookie is HttpOnly, so app code cannot read
        // it, and every *.lemma.localhost host is served by this install's own
        // backend. An app acting as the signed-in user is the feature, and it
        // is what already happens on the web build.
        ("SESSION_COOKIE_DOMAIN", ".lemma.localhost".to_owned()),
        // Empty, and deliberately not absent: it names the scheme the line
        // above replaced.
        //
        // Widening the cookie domain does not replace the cookies a browser
        // already holds, it mints a second set beside them. An install that
        // signed in on v0.7.0 -- which rendered `SESSION_COOKIE_DOMAIN` empty,
        // so host-only on app.lemma.localhost -- then upgraded to this, sends
        // both, and SuperTokens refuses the pair with `The request contains
        // multiple session cookies`, a 500. The SDK treats a 500 as retryable
        // and asks again per query, so the console fills and the workspace
        // never settles. One install logged 30 of those refusals and 17 500s.
        //
        // SuperTokens reads the empty string as "the previous cookies were
        // host-only" and clears them on the next refresh, which is precisely
        // the migration being made here. `None` would mean "there was no
        // previous scheme" and clear nothing, so the backend setting keeps a
        // blank string rather than folding it to None like its neighbours.
        //
        // Removable once no install can still be carrying v0.7.0 cookies.
        ("SESSION_COOKIE_OLDER_DOMAIN", String::new()),
        // The other half, and only meaningful together with the domain above:
        // apps call the API through their own origin so the request is
        // first-party. Off by default in the backend, because on a real domain
        // an app subdomain and the API host are already same-site and this
        // would widen the refresh cookie for nothing.
        ("APP_API_VIA_APP_ORIGIN", "true".to_owned()),
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

    // What decides whether the one-time setups need to run again.
    //
    // Migrations ship inside the pack, so the release identifies them -- except
    // in source mode, where one version spans many edits, so the revision files
    // are fingerprinted too. Adding a Composio key must still pick up its apps
    // on the next start, which is why that is part of the catalog's stamp
    // rather than the release alone.
    let migrations_fingerprint = migrations_fingerprint(&bindings.backend_dir);
    let backend_env_has_composio_key = backend_env
        .get("COMPOSIO_API_KEY")
        .is_some_and(|value| !value.trim().is_empty());

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
        // Deliberately NOT widened to match SESSION_COOKIE_DOMAIN.
        //
        // This is the domain the *browser* SDK writes its own cookies to, and
        // those are written with `document.cookie` -- so `sFrontToken`,
        // `sAntiCsrf` and `st-last-access-token-update` are readable and
        // writable by any script on any host in scope. Widening it put them on
        // `.lemma.localhost`, where a pod app -- user-authored code on a
        // sibling host -- could overwrite the workspace's copies at the same
        // name, domain and path. Clearing `sFrontToken` signs the user out of
        // Lemma itself; setting its expiry far ahead stops the workspace ever
        // refreshing, so every screen 401s with no way back.
        //
        // Host-only is also simply correct: each origin's SDK keeps its own
        // copy from its own responses, and the cookies that actually have to
        // be shared -- the HttpOnly session pair -- are shared by
        // SESSION_COOKIE_DOMAIN, which app code cannot read or write.
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
                // Migrations ship inside the pack, so the pack's identity is
                // exactly what decides whether there is anything new to apply.
                // Alembic would work this out for itself in one `SELECT` --
                // the cost is `env.py` importing the whole ORM graph before it
                // can, several seconds on every single start.
                //
                // The revisions are hashed in as well as the release, because
                // a source-mode run keeps one version across many edits, and a
                // developer adding a migration must not have it skipped.
                "stamp": setup_stamp(&[&release_version, &migrations_fingerprint]),
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
                // The pack, plus whether a Composio key is present. The second
                // half preserves the behaviour the comment above describes: a
                // user who adds a key later gets the Composio apps on the very
                // next start, because adding one changes this stamp.
                "stamp": setup_stamp(&[
                    &release_version,
                    if backend_env_has_composio_key { "composio" } else { "native-only" },
                ]),
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
    // Absent, not unreadable -- and that is not automatically a first run. A
    // restore that skipped an owner-only file, or a half-finished manual
    // cleanup, leaves the data behind and takes the key. Minting silently there
    // is the same permanent loss the corrupt path is careful about, arrived at
    // more quietly.
    else if path
        .parent()
        .is_some_and(crate::paths::installation_has_data)
    {
        let root = path.parent().expect("checked just above");
        crate::paths::require_data_reset(
            root,
            "this installation's secret is missing, and anything encrypted with it can no \
             longer be read",
        )?;
        healed.push(
            "the installation secret was missing while local data was still present; a new \
             one was created and the existing encrypted data cannot be decrypted with it"
                .to_owned(),
        );
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

/// One stamp value from the things a setup's result depends on.
///
/// Hashed rather than concatenated so the manifest never carries a path or a
/// key's presence in readable form, and so the value stays a fixed width
/// whatever goes into it.
fn setup_stamp(parts: &[&str]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part.as_bytes());
        // Length-delimited: without this, ("ab", "c") and ("a", "bc") hash the
        // same, and two different states would share a stamp.
        hasher.update([0u8]);
    }
    format!("{:x}", hasher.finalize())
}

/// A fingerprint of the migration revisions a pack carries.
///
/// Names only, not contents: the file set changes when a revision is added or
/// removed, which is the case that matters, and reading every file on each
/// start would trade one cost for another. Empty when the directory cannot be
/// read, which makes the stamp depend on the release alone -- the conservative
/// direction, since a stamp that cannot be computed should not become a stamp
/// that matches.
fn migrations_fingerprint(backend_dir: &Path) -> String {
    let Ok(entries) = fs::read_dir(backend_dir.join("migrations/versions")) else {
        return String::new();
    };
    let mut names: Vec<String> = entries
        .flatten()
        .filter_map(|entry| entry.file_name().into_string().ok())
        .filter(|name| name.ends_with(".py"))
        .collect();
    names.sort();
    let mut hasher = Sha256::new();
    for name in &names {
        hasher.update(name.as_bytes());
        hasher.update([0u8]);
    }
    format!("{:x}", hasher.finalize())
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

    /// Every path this file probes for is one the contract names.
    ///
    /// The producer of a host pack is a Python script run by a release job; the
    /// consumer is this file, and it hard-codes a dozen paths. Nothing checked
    /// that the two agreed, and nothing could: a PR runs this file's tests
    /// against a fixture the same PR wrote, while the pack is built somewhere
    /// else on a different trigger. A rename lands green on both sides and is
    /// found by whoever installs the release, as `NotFound` and the name of a
    /// file they have never heard of.
    ///
    /// So both sides assert against one committed artifact -- the rule
    /// docs/testing.md states for when a stub is allowed to exist at all. The
    /// Python half of this pair is
    /// `tests/test_build_local_host_pack.py::test_the_builder_writes_every_path_the_app_looks_for`.
    ///
    /// Asserted as a set equality rather than containment, in both directions.
    /// A candidate here that the contract does not name is a path the producer
    /// has never been told to write; a candidate there that this file does not
    /// probe is a fallback nobody would ever reach.
    #[test]
    fn the_packaged_layout_matches_the_committed_contract() {
        let contract: Value =
            serde_json::from_str(include_str!("../../contracts/host-pack-layout.json")).unwrap();

        // What this file actually probes for, read out of its own source so the
        // list cannot be maintained twice.
        let source = include_str!("native_host_pack.rs").replace("\r\n", "\n");
        let packaged = {
            let start = source
                .find("fn packaged_bindings(")
                .expect("packaged_bindings exists");
            let end = source[start..]
                .find("\nfn source_bindings(")
                .expect("source_bindings follows it");
            &source[start..start + end]
        };

        for entry in contract["required"].as_array().unwrap() {
            let what = entry["what"].as_str().unwrap();
            for candidate in entry["candidates"].as_array().unwrap() {
                let candidate = candidate.as_str().unwrap();
                assert!(
                    packaged.contains(&format!("\"{candidate}\"")),
                    "the contract promises {what} at {candidate}, which this file never looks for",
                );
            }
        }

        // And nothing probed for is absent from the contract. `required_file`
        // takes its candidates as string literals, so they are exactly the
        // quoted paths in this function.
        let promised: std::collections::BTreeSet<&str> = contract["required"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|entry| entry["candidates"].as_array().unwrap())
            .map(|candidate| candidate.as_str().unwrap())
            .collect();
        for line in packaged.lines() {
            let trimmed = line.trim();
            let Some(rest) = trimmed.strip_prefix('"') else {
                continue;
            };
            let Some(path) = rest.strip_suffix("\",") else {
                continue;
            };
            if !path.contains('/') {
                continue;
            }
            assert!(
                promised.contains(path),
                "this file probes for {path}, which the contract does not promise",
            );
        }

        // The derived paths are joins rather than probes, so they are checked
        // as substrings -- but of the shipping half of the file only. Searching
        // the whole thing let a path be "found" in this module's own fixtures,
        // which is a test satisfying itself.
        let shipping = &source[..source
            .find("#[cfg(test)]")
            .expect("this file has a test module")];
        for entry in contract["derived"].as_array().unwrap() {
            if !entry["named_by_consumer"].as_bool().unwrap_or(true) {
                continue;
            }
            let path = entry["path"].as_str().unwrap();
            let (parent, leaf) = path.rsplit_once('/').unwrap_or(("", path));
            assert!(
                shipping.contains(leaf),
                "the contract names {path}, which nothing in this file joins ({parent})",
            );
        }
    }

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
        // Wide enough to cover the app subdomains. Host-only here is what made
        // every pod app load unauthenticated; see the note beside the value.
        assert_eq!(
            manifest["services"][0]["env"]["SESSION_COOKIE_DOMAIN"],
            ".lemma.localhost"
        );
        assert_eq!(
            manifest["services"][0]["env"]["API_URL"],
            format!("http://app.lemma.localhost:{backend_port}")
        );
        // And the browser-visible one is NOT widened with it. These cookies are
        // written by `document.cookie`, so a shared domain lets a pod app
        // overwrite the workspace's session state and sign the user out.
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

    /// Changing the cookie domain has to say what it replaced.
    ///
    /// A widened `SESSION_COOKIE_DOMAIN` does not replace the cookies a browser
    /// already holds; it mints a second set beside them, and SuperTokens
    /// refuses the pair on refresh with a 500 that the SDK retries for ever.
    /// `SESSION_COOKIE_OLDER_DOMAIN` is what clears the old one, so the two
    /// settings are only correct together -- asserted here rather than left to
    /// whoever next edits the domain.
    ///
    /// Empty is the value, not a missing one: it is how SuperTokens spells
    /// "the previous cookies were host-only", which is what v0.7.0 rendered.
    #[test]
    fn a_widened_cookie_domain_declares_the_scheme_it_replaced() {
        let root = tempdir().unwrap();
        let pack = root.path().join("pack");
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
        let env = &manifest["services"][0]["env"];

        let domain = env["SESSION_COOKIE_DOMAIN"].as_str().unwrap();
        assert!(
            !domain.is_empty(),
            "a host-only cookie does not reach the app subdomains"
        );
        let older = env["SESSION_COOKIE_OLDER_DOMAIN"]
            .as_str()
            .unwrap_or_else(|| {
                panic!(
                    "SESSION_COOKIE_DOMAIN is {domain}, so the scheme it replaced \
                 has to be declared or an upgraded install carries both"
                )
            });
        assert_eq!(
            older, "",
            "v0.7.0 rendered a host-only cookie, which SuperTokens spells as \
             the empty string"
        );
    }

    /// The session cookie has to be in scope on the hosts apps are served from.
    ///
    /// Derived from `APP_BASE_DOMAIN` rather than restating `.lemma.localhost`,
    /// so moving apps to another host without moving the cookie fails here
    /// instead of shipping. That pairing is the whole fix: WebKit will not send
    /// a cookie to a host it is not scoped for, and it will not send one
    /// cross-site on `.localhost` at all -- so an app that is out of scope is an
    /// app that loads permanently signed out.
    #[test]
    fn the_session_cookie_reaches_the_hosts_apps_are_served_from() {
        let root = tempdir().unwrap();
        let pack = root.path().join("pack");
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
        let env = &manifest["services"][0]["env"];

        let cookie_domain = env["SESSION_COOKIE_DOMAIN"].as_str().unwrap();
        let app_base = env["APP_BASE_DOMAIN"].as_str().unwrap();
        let app_host = app_base.split(':').next().unwrap();

        assert!(
            !cookie_domain.is_empty(),
            "a host-only cookie never reaches {app_host}, which is what made \
             every pod app load unauthenticated"
        );
        // A leading dot covers subdomains; the app is at <slug>.<app_base>.
        let scope = cookie_domain.strip_prefix('.').unwrap_or(cookie_domain);
        assert!(
            app_host == scope || app_host.ends_with(&format!(".{scope}")),
            "apps are served under {app_host} but the session cookie is scoped \
             to {cookie_domain}, so it is never sent to them"
        );
        // The API has to be inside the same scope, or the app's own-origin
        // calls are the only ones that work and the frontend signs out.
        let api_host = env["API_URL"]
            .as_str()
            .unwrap()
            .trim_start_matches("http://")
            .split(':')
            .next()
            .unwrap()
            .to_owned();
        assert!(
            api_host == scope || api_host.ends_with(&format!(".{scope}")),
            "the API at {api_host} is outside the cookie scope {cookie_domain}"
        );

        // Both halves or neither. A widened cookie with apps still calling the
        // API host is measured *not* to work -- WebKit drops it as third-party
        // whatever the Domain says -- so shipping one alone is shipping the bug
        // plus a wider cookie.
        assert_eq!(
            env["APP_API_VIA_APP_ORIGIN"], "true",
            "the cookie is scoped for the app hosts but apps are still pointed \
             at the API host, where the request is cross-site and carries none"
        );
    }

    /// Every URL a sandbox is given resolves inside a sandbox.
    ///
    /// `app.lemma.localhost` resolves on the Mac and nowhere else: `*.localhost`
    /// is a host resolver convention, and a Linux container in the VM has never
    /// heard of it. `host.lemma.internal` is what guestd `--add-host`es into
    /// every workload container.
    ///
    /// Asserted over every callback variable at once rather than one by one,
    /// because the bug was an *absent* entry: a test naming only the variables
    /// that exist cannot fail for the one that does not.
    #[test]
    fn no_sandbox_is_given_an_address_only_the_mac_can_resolve() {
        let root = tempdir().unwrap();
        let pack = root.path().join("pack");
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

        let env = &manifest["services"][0]["env"];
        let object = env.as_object().expect("services carry an env map");

        // Anything a *sandbox* uses to call back. The workspace pair was
        // right; the function one did not exist.
        let sandbox_facing: Vec<&String> = object
            .keys()
            .filter(|name| {
                name.ends_with("_URL")
                    && (name.contains("CALLBACK") || name.contains("RUNTIME_GATEWAY"))
            })
            .collect();
        assert!(
            sandbox_facing
                .iter()
                .any(|name| name.as_str() == "FUNCTION_RUNTIME_GATEWAY_URL"),
            "functions get no gateway URL, so the dispatcher falls back to \
             api_url and every call dies in DNS: {sandbox_facing:?}",
        );

        for name in sandbox_facing {
            let value = env[name].as_str().unwrap_or_default();
            assert!(
                !value.contains(".localhost"),
                "{name} is {value}, and .localhost resolves only on the host",
            );
            assert!(
                value.is_empty() || value.contains("host.lemma.internal"),
                "{name} is {value}; a sandbox can only reach the host through \
                 host.lemma.internal",
            );
        }
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

    /// A secret that vanished beside surviving data stops the install.
    ///
    /// `installation_secret` derives the Fernet key for every encrypted column.
    /// Reminting it while the data directory is still there does not fail --
    /// that is the problem. Rows decrypt to garbage, and the only sign is
    /// whatever breaks first, weeks later.
    #[test]
    fn a_missing_installation_secret_beside_real_data_demands_a_reset() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path();
        std::fs::create_dir_all(root.join("data/files")).unwrap();
        std::fs::write(root.join("data/files/uploaded"), b"a user's file").unwrap();
        let path = root.join("host.secrets.json");

        let mut healed = Vec::new();
        let secrets = load_or_create_host_secrets(&path, &mut healed).unwrap();

        assert_eq!(
            secrets.installation_secret.len(),
            64,
            "a key is still minted"
        );
        assert!(
            crate::paths::data_reset_reason(root).is_some(),
            "the install must stop with a reset offered, not carry on quietly",
        );
        assert_eq!(healed.len(), 1);
        assert!(healed[0].contains("missing"), "{}", healed[0]);
    }

    /// And a genuine first run says nothing at all.
    #[test]
    fn a_first_run_mints_its_secret_without_ceremony() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path();
        std::fs::create_dir_all(root.join("data/files")).unwrap();
        let path = root.join("host.secrets.json");

        let mut healed = Vec::new();
        load_or_create_host_secrets(&path, &mut healed).unwrap();

        assert!(healed.is_empty(), "nothing was lost: {healed:?}");
        assert!(
            crate::paths::data_reset_reason(root).is_none(),
            "a first run must not be told to reset the data it does not have",
        );
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
