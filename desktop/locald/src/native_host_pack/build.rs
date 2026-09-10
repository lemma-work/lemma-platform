//! Writing the manifest the host processes are started from.

use super::*;

pub(crate) fn build(
    paths: &LocalPaths,
    pack_root: &Path,
    material: &ManagedManifestMaterial,
    ports: NetworkPorts,
    source: Option<&SourceLayout>,
    healed: &mut Vec<String>,
    domain: &LocalDomain,
) -> io::Result<Value> {
    validate_hex_secret("postgres password", &material.postgres_password)?;
    validate_hex_secret("Redis password", &material.redis_password)?;

    // A checkout carries no release.json or pack.json of its own: its app code
    // is local, but the infrastructure and sandbox images it runs against are
    // still the pinned ones, borrowed from an installed release.
    let (bindings, release_path) = match source {
        Some(layout) => {
            let root = canonicalize_for_children(&layout.root).map_err(|error| {
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
            let root = canonicalize_for_children(pack_root).map_err(|error| {
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
    let host = domain.frontend_host();
    let frontend_origin = format!("http://{host}:{frontend_port}");
    let backend_origin = format!("http://{host}:{backend_port}");
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
        ("SESSION_COOKIE_DOMAIN", domain.cookie_domain()),
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
        // Only where the app host and the API host are *not* already same-site.
        //
        // On `*.localhost` a browser can derive no registrable domain, so those
        // two are different sites and an app's call to the API is third-party --
        // hence the same-origin `/_lemma` door, and the widened refresh cookie
        // that makes it work. On a real registrable domain they are same-site
        // already and the door buys nothing but a whole-API alias on the origin
        // that renders user-authored HTML.
        (
            "APP_API_VIA_APP_ORIGIN",
            if domain.frames_carry_cookies() {
                "false"
            } else {
                "true"
            }
            .to_owned(),
        ),
        (
            "APP_BASE_DOMAIN",
            format!("{}:{backend_port}", domain.apps_domain()),
        ),
        ("CORS_ORIGIN_REGEX", domain.cors_origin_regex()),
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
