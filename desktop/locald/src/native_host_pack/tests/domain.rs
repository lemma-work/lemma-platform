//! One configured domain, and everything that has to move with it.

use super::*;

/// The workspace, the apps and the cookie all sit under `lemma.localhost`.
///
/// And the `/_lemma` door is on: WebKit derives no site wider than the host
/// from `*.localhost`, so an app calling the API host directly is cross-site
/// and carries no session. The macOS alias depends on the door too -- a framed
/// alias calls its own origin, and only a relative `apiUrl` makes that true.
#[test]
fn the_workspace_the_apps_and_the_cookie_share_lemma_localhost() {
    let root = tempdir().unwrap();
    let pack = root.path().join("pack");
    fixture(&pack);
    let paths = LocalPaths::new(root.path().join("locald"));
    paths.ensure().unwrap();
    let manifest = build(
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
        &LocalDomain::current(),
    )
    .unwrap();
    let manifest: Value = serde_json::to_value(&manifest).unwrap();
    let env = &manifest["services"][0]["env"];

    assert_eq!(env["SESSION_COOKIE_DOMAIN"], ".lemma.localhost");
    assert_eq!(
        env["APP_BASE_DOMAIN"]
            .as_str()
            .unwrap()
            .split(':')
            .next()
            .unwrap(),
        "apps.lemma.localhost"
    );
    assert!(env["API_URL"]
        .as_str()
        .unwrap()
        .starts_with("http://app.lemma.localhost:"));
    assert_eq!(env["APP_API_VIA_APP_ORIGIN"], "true");
    // No public-DNS name survives anywhere in what the backend is told.
    let rendered = serde_json::to_string(env).unwrap();
    assert!(!rendered.contains("sslip"), "{rendered}");
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

    // Always on: see `the_workspace_the_apps_and_the_cookie_share_lemma_localhost`.
    assert_eq!(env["APP_API_VIA_APP_ORIGIN"], "true");
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

    let base = LocalDomain::current().base().to_owned();
    for name in sandbox_facing {
        let value = env[name].as_str().unwrap_or_default();
        assert!(
            !value.contains(".localhost"),
            "{name} is {value}, and .localhost resolves only on the host",
        );
        // And not this install's own base domain either, whatever it is.
        //
        // The `.localhost` check above stopped being the whole story when
        // the base domain became a runtime choice. A loopback wildcard is
        // worse than an unresolvable name, not better: inside a container
        // it resolves perfectly well, to 127.0.0.1 -- which is the
        // container itself. The failure is then a connection refused, or
        // worse a connection to whatever that container happens to be
        // running, rather than a DNS error naming the problem.
        assert!(
            !value.contains(&base),
            "{name} is {value}, and {base} answers this Mac's loopback --                  inside a container that address is the container",
        );
        assert!(
            value.is_empty() || value.contains("host.lemma.internal"),
            "{name} is {value}; a sandbox can only reach the host through \
             host.lemma.internal",
        );
    }
}

/// The backend's addresses, as `desktop/contracts/host-pack-urls.json`
/// records them for the Python side to be tested against.
///
/// Two network perspectives share one backend. A sandbox reaches it through
/// `host.lemma.internal`, which only guestd's containers can resolve; an agent
/// running on the Mac reaches it through the install's own domain. Pinned here
/// so the backend's host-agent test reads what the host pack actually emits,
/// not a URL that happens to work from both sides.
#[test]
fn the_backend_url_contract_matches_what_the_host_pack_emits() {
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
    let env = manifest["services"][0]["env"]
        .as_object()
        .expect("services carry an env map");

    let contract: Value =
        serde_json::from_str(include_str!("../../../../contracts/host-pack-urls.json")).unwrap();
    let recorded = contract["backend_env"]
        .as_object()
        .expect("the contract records the backend's URL environment");
    let base = LocalDomain::current().base().to_owned();
    let emitted: serde_json::Map<String, Value> = env
        .iter()
        .filter(|(name, _)| recorded.contains_key(name.as_str()) || is_backend_url_variable(name))
        .map(|(name, value)| {
            let text = value.as_str().unwrap_or_default();
            (name.clone(), Value::from(templated(text, &base)))
        })
        .collect();
    assert_eq!(
        &emitted, recorded,
        "the host pack's URL environment changed; update \
         desktop/contracts/host-pack-urls.json so the backend is tested against it"
    );
}

/// The variables the backend reads its own addresses from.
fn is_backend_url_variable(name: &str) -> bool {
    matches!(
        name,
        "API_URL" | "FRONTEND_URL" | "AUTH_FRONTEND_URL" | "CLI_API_URL" | "CLI_AUTH_FRONTEND_URL"
    ) || (name.starts_with("WORKSPACE_CALLBACK_") && name.ends_with("_URL"))
}

/// `value` with this install's base domain and every port made placeholders,
/// so the contract does not depend on which ports this machine was given.
fn templated(value: &str, base: &str) -> String {
    let value = value.replace(base, "{base}");
    let mut out = String::with_capacity(value.len());
    let mut chars = value.chars().peekable();
    while let Some(ch) = chars.next() {
        out.push(ch);
        if ch == ':' && chars.peek().is_some_and(char::is_ascii_digit) {
            while chars.peek().is_some_and(char::is_ascii_digit) {
                chars.next();
            }
            out.push_str("{port}");
        }
    }
    out
}
