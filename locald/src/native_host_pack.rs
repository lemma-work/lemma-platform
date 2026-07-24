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

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct HostSecrets {
    agentbox_api_key: String,
}

pub(crate) fn prepare(
    paths: &LocalPaths,
    pack_root: &Path,
    material: ManagedManifestMaterial,
) -> io::Result<PathBuf> {
    let ports = load_or_allocate(paths)?;
    let manifest = build(paths, pack_root, &material, ports)?;
    let destination = paths.root.join("host-pack.json");
    write_private_atomic(&destination, &serde_json::to_vec_pretty(&manifest)?)?;
    Ok(destination)
}

fn build(
    paths: &LocalPaths,
    pack_root: &Path,
    material: &ManagedManifestMaterial,
    ports: NetworkPorts,
) -> io::Result<Value> {
    validate_hex_secret("postgres password", &material.postgres_password)?;
    validate_hex_secret("Redis password", &material.redis_password)?;
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

    let python = required_file(
        &root,
        "backend Python",
        &[
            "backend/python/bin/python3",
            "backend/python/bin/python",
            "backend/python/python.exe",
        ],
    )?;
    let node = required_file(
        &root,
        "frontend Node.js",
        &["frontend/node/bin/node", "frontend/node/node.exe"],
    )?;
    let frontend_launcher = required_file(
        &root,
        "frontend launcher",
        &["frontend/frontend-launcher.mjs"],
    )?;
    let frontend_server = required_file(
        &root,
        "Next.js standalone server",
        &[
            "frontend/server.js",
            "frontend/app/server.js",
            "frontend/lemma-frontend/server.js",
        ],
    )?;
    let backend_dir = root.join("backend");
    let frontend_dir = root.join("frontend");

    let release: Value = read_json(&root.join("release.json"), "native release manifest")?;
    let release_version = required_string(&release, "version", "native release manifest")?;
    let pack: Value = read_json(&root.join("pack.json"), "native host pack marker")?;
    if pack.get("release").and_then(Value::as_str) != Some(release_version.as_str()) {
        return Err(invalid(
            "native host pack marker does not match its release",
        ));
    }
    let workspace_image = pull_ref(
        release.pointer("/images/agentbox_workspace"),
        "AgentBox workspace image",
    )?;
    let function_image = pull_ref(
        release.pointer("/images/agentbox_function"),
        "AgentBox function image",
    )?;
    let postgres_image = pull_ref(release.pointer("/infra/postgres"), "Postgres image")?;
    let redis_image = pull_ref(release.pointer("/infra/redis"), "Redis image")?;
    let supertokens_image = pull_ref(release.pointer("/infra/supertokens"), "SuperTokens image")?;
    for (label, image) in [
        ("AgentBox workspace", &workspace_image),
        ("AgentBox function", &function_image),
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

    let secrets = load_or_create_host_secrets(&paths.root.join("host.secrets.json"))?;
    let runtime_key = URL_SAFE.encode(Sha256::digest(
        [
            secrets.agentbox_api_key.as_bytes(),
            b"lemma-agentbox-runtime-credential-v1",
        ]
        .concat(),
    ));
    let function_runtime_secret = URL_SAFE.encode(Sha256::digest(
        [
            secrets.agentbox_api_key.as_bytes(),
            b"lemma-function-runtime-secret-v1",
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
        ("DOCUMENT_PROCESSOR", "markitdown".to_owned()),
        ("AGENTBOX_ENVIRONMENT", "local".to_owned()),
        (
            "AGENTBOX_API_URL",
            format!("http://127.0.0.1:{backend_port}/internal/agentbox"),
        ),
        (
            "AGENTBOX_PUBLIC_URL",
            format!("{backend_origin}/internal/agentbox"),
        ),
        ("AGENTBOX_API_KEY", secrets.agentbox_api_key),
        ("AGENTBOX_PROVIDER", "lemma_local".to_owned()),
        ("AGENTBOX_RUNTIME_CREDENTIAL_KEY", runtime_key),
        ("FUNCTION_RUNTIME_SECRET", function_runtime_secret),
        ("AGENTBOX_WORKSPACE_IMAGE", workspace_image),
        ("AGENTBOX_FUNCTION_IMAGE", function_image),
        (
            "AGENTBOX_STATE_DATABASE_URL",
            format!(
                "postgresql+asyncpg://postgres:{}@127.0.0.1:{POSTGRES_PORT}/agentbox",
                material.postgres_password
            ),
        ),
        ("AGENTBOX_AUTO_CREATE_SCHEMA", "true".to_owned()),
        ("AGENTBOX_ADD_HOST_GATEWAY", "false".to_owned()),
        ("AGENTBOX_HOST_ALIAS", "host.lemma.internal".to_owned()),
        ("AGENTBOX_LOCAL_SCOPE", "lemma-local:managed".to_owned()),
        ("AGENTBOX_LOCAL_WORKSPACE_MEMORY", "2g".to_owned()),
        ("AGENTBOX_LOCAL_WORKSPACE_CPUS", "2".to_owned()),
        ("AGENTBOX_LOCAL_FUNCTION_MEMORY", "2g".to_owned()),
        ("AGENTBOX_LOCAL_FUNCTION_CPUS", "4".to_owned()),
        ("AGENTBOX_LOCAL_CALLBACK_REQUIRED", "true".to_owned()),
        (
            "AGENTBOX_LOCAL_CALLBACK_URL",
            format!("http://host.lemma.internal:{backend_port}"),
        ),
        ("AGENTBOX_LOCAL_RUNTIME_TIMEOUT_SECONDS", "600".to_owned()),
        (
            "AGENTBOX_LOCAL_RUNTIME_CLI",
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
    ]);
    let browser_sdk = backend_dir.join("assets/browser-sdk/lemma-client.js");
    let browser_ui = backend_dir.join("assets/browser-sdk/lemma-ui.js");
    let skills = backend_dir.join("assets/lemma-skills");
    if browser_sdk.is_file() {
        backend_env.insert("BROWSER_SDK_PATH", path_text(&browser_sdk)?);
    }
    if browser_ui.is_file() {
        backend_env.insert("BROWSER_UI_PATH", path_text(&browser_ui)?);
    }
    if skills.is_dir() {
        backend_env.insert("LEMMA_SKILLS_ROOT", path_text(&skills)?);
    }

    let frontend_env = BTreeMap::from([
        ("NODE_ENV", "production".to_owned()),
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
        "setup": [{
            "id": "migrations",
            "command": [path_text(&python)?, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            "cwd": path_text(&backend_dir)?,
            "env": backend_env,
            "timeout_seconds": 300,
            "max_attempts": 3,
            "retry_backoff_seconds": 2,
        }],
        "services": [
            {
                "id": "backend",
                "command": [path_text(&python)?, "-m", "uvicorn", "local_app:app", "--host", "127.0.0.1", "--port", backend_port.to_string(), "--ws", "websockets-sansio"],
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
                "command": [path_text(&node)?, path_text(&frontend_launcher)?, path_text(&frontend_server)?],
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

fn load_or_create_host_secrets(path: &Path) -> io::Result<HostSecrets> {
    if path.is_file() {
        ensure_private_file(path)?;
        let secrets: HostSecrets = serde_json::from_slice(&fs::read(path)?)?;
        validate_hex_secret("AgentBox API key", &secrets.agentbox_api_key)?;
        return Ok(secrets);
    }
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("secure randomness failed: {error}")))?;
    let secrets = HostSecrets {
        agentbox_api_key: bytes.iter().map(|byte| format!("{byte:02x}")).collect(),
    };
    write_private_atomic(path, &serde_json::to_vec(&secrets)?)?;
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
                "agentbox_workspace": {
                  "ref": "workspace",
                  "digest": "sha256:workspace"
                },
                "agentbox_function": {
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
        assert!(!manifest["services"][0]["command"]
            .as_array()
            .unwrap()
            .iter()
            .any(|argument| argument == "--no-access-log"));
        assert_eq!(
            manifest["services"][0]["env"]["WORKSPACE_CALLBACK_API_URL"],
            format!("http://host.lemma.internal:{backend_port}")
        );
        assert_eq!(
            manifest["services"][0]["env"]["AGENTBOX_PROVIDER"],
            "lemma_local"
        );
        assert!(
            manifest["services"][0]["env"]["AGENTBOX_STATE_DATABASE_URL"]
                .as_str()
                .unwrap()
                .starts_with("postgresql+asyncpg://")
        );
        assert!(
            manifest["services"][0]["env"]["FUNCTION_RUNTIME_SECRET"]
                .as_str()
                .unwrap()
                .len()
                >= 32
        );
        assert_eq!(
            manifest["services"][0]["env"]["DOCUMENT_PROCESSOR"],
            "markitdown"
        );
        assert_eq!(
            manifest["services"][0]["env"]["HOME"],
            path_text(&paths.root.join("state/home")).unwrap()
        );
        assert_eq!(
            manifest["services"][0]["env"]["XDG_CACHE_HOME"],
            path_text(&paths.root.join("state/cache")).unwrap()
        );
        assert_eq!(
            manifest["services"][0]["env"]["TLDEXTRACT_CACHE"],
            path_text(&paths.root.join("state/cache/tldextract")).unwrap()
        );
        assert_eq!(
            manifest["services"][0]["env"]["SUPERTOKENS_TLDEXTRACT_DISABLE_HTTP"],
            "1"
        );
        assert_eq!(
            manifest["services"][0]["env"]["LOCAL_EMBEDDING_CACHE_DIR"],
            path_text(&paths.root.join("state/cache/fastembed")).unwrap()
        );
        assert!(paths.root.join("state/cache/tldextract").is_dir());
        assert!(paths.root.join("state/cache/fastembed").is_dir());
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
            manifest["services"][0]["env"]["AGENTBOX_LOCAL_WORKSPACE_MEMORY"],
            "2g"
        );
        assert_eq!(
            manifest["services"][0]["env"]["AGENTBOX_LOCAL_WORKSPACE_CPUS"],
            "2"
        );
        assert_eq!(
            manifest["services"][0]["env"]["AGENTBOX_LOCAL_FUNCTION_CPUS"],
            "4"
        );
        assert_eq!(
            manifest["services"][0]["env"]["AGENTBOX_LOCAL_RUNTIME_TIMEOUT_SECONDS"],
            "600"
        );
        assert_eq!(
            manifest["services"][0]["env"]["AGENTBOX_LOCAL_CALLBACK_URL"],
            format!("http://host.lemma.internal:{backend_port}")
        );
        assert_eq!(
            manifest["services"][0]["env"]["AGENTBOX_WORKSPACE_IMAGE"],
            "workspace@sha256:workspace"
        );
        assert_eq!(
            manifest["services"][0]["env"]["AGENTBOX_FUNCTION_IMAGE"],
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
        )
        .unwrap_err();
        assert!(error.to_string().contains("Redis image must be pinned"));
    }
}
