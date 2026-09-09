//! One configured domain, and everything that has to move with it.

use super::*;

/// A configured domain moves every host together, and drops the workaround.
///
/// The point of moving off `*.localhost` is that a browser can then derive
/// a registrable domain covering both the workspace and the app hosts, so a
/// framed pod app is same-site and can hold a session. That only holds if
/// the whole arrangement moves at once: a cookie still scoped to the old
/// domain, or an app host under a different one, and the frame is back to
/// being third-party with nothing to show for the change.
#[test]
fn a_configured_domain_moves_the_workspace_the_apps_and_the_cookie_together() {
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
        &LocalDomain::parse(Some("sslip")),
    )
    .unwrap();
    let manifest: Value = serde_json::to_value(&manifest).unwrap();
    let env = &manifest["services"][0]["env"];

    let cookie = env["SESSION_COOKIE_DOMAIN"].as_str().unwrap();
    let app_base = env["APP_BASE_DOMAIN"].as_str().unwrap();
    let app_host = app_base.split(':').next().unwrap();
    let api_host = env["API_URL"]
        .as_str()
        .unwrap()
        .trim_start_matches("http://")
        .split(':')
        .next()
        .unwrap()
        .to_owned();

    assert_eq!(cookie, ".127.0.0.1.sslip.io");
    assert_eq!(app_host, "apps.127.0.0.1.sslip.io");
    assert_eq!(api_host, "app.127.0.0.1.sslip.io");
    // Both hosts inside the cookie's scope, or the app is signed out.
    let scope = cookie.trim_start_matches('.');
    assert!(app_host.ends_with(scope), "{app_host} is outside {cookie}");
    assert!(api_host.ends_with(scope), "{api_host} is outside {cookie}");

    // ...and the `*.localhost` workaround goes away with it. On a real
    // registrable domain the app host and the API host are already
    // same-site, so aliasing the whole API under `/_lemma` on the origin
    // that renders user-authored HTML -- and widening the refresh cookie to
    // make that work -- buys nothing.
    assert_eq!(env["APP_API_VIA_APP_ORIGIN"], "false");
}

/// The arrangement a laptop with no network gets, asserted on its own.
///
/// `from_env` falls back here when the public wildcard does not resolve, so
/// this is not an exotic path -- it is every offline launch. The sibling
/// test above pins the same-site arrangement; without this one the fallback
/// would only ever be rendered by tests that happen to run offline, which
/// is the same as not testing it.
#[test]
fn the_offline_fallback_renders_the_workaround_that_makes_it_work() {
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
        &LocalDomain::parse(Some(crate::local_domain::LOCALHOST_BASE)),
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
    // Here the door is the only thing that works: a browser derives no
    // registrable domain from `*.localhost`, so an app calling the API host
    // directly is cross-site and carries no session. Turning this off
    // without also moving the base domain is the bug that shipped twice.
    assert_eq!(env["APP_API_VIA_APP_ORIGIN"], "true");
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

    // The app-origin door exactly where it is needed, and nowhere else.
    //
    // On `*.localhost` a browser derives no registrable domain, so an app's
    // call to the API is cross-site and carries no cookie whatever the
    // Domain says -- measured. Both halves are required there: widening the
    // cookie alone ships the bug plus a wider cookie.
    //
    // On a real registrable domain the two hosts are same-site already, and
    // the door would only alias the whole API under `/_lemma` on the origin
    // that renders user-authored HTML, widening the refresh cookie to do it.
    let domain = LocalDomain::from_env();
    assert_eq!(
        env["APP_API_VIA_APP_ORIGIN"],
        if domain.frames_carry_cookies() {
            "false"
        } else {
            "true"
        },
        "the app-origin door has to follow whether {} is same-site with its \
         app hosts",
        domain.base()
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

    let base = LocalDomain::from_env().base().to_owned();
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
