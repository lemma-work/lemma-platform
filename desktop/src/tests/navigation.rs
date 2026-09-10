use super::*;

#[test]
fn the_workspace_origin_reaches_local_settings_and_nothing_else() {
    // The workspace is a remote origin to Tauri - locald serves it over
    // http, and the hosted build loads lemma.work - so without this
    // capability its Local settings button is silently rejected by the ACL.
    //
    // What it may reach is deliberately short: Local settings, this
    // computer's Agent Host, the AI provider, and how the sandbox image
    // download is going. The provider one was added because onboarding
    // cannot honestly ask "which model?" and then send the user to a
    // different window for the answer — and it is safe to add precisely
    // because `configure_ai_provider` reaches `config.set-ai`, which merges
    // that one section. `allow-apply-operator-config`, which would let the
    // same page rewrite sharing and surfaces, stays out.
    //
    // `allow-sandbox-image-status` is the mildest: it reads two strings the
    // shell already holds and changes nothing at all. It is here because the
    // download runs behind a workspace the user is already in, so the
    // workspace is the only surface that can say it is happening.
    //
    // The three `conversation-folder` ones are the least mild, and they are
    // the reason this list is asserted rather than trusted. One of them raises
    // a native folder dialog, and `https://lemma.work` is in this capability's
    // remote URLs -- so granted alone they would let a hosted page open a
    // picker on somebody's machine and read paths back out of it. Each refuses
    // unless `current_mode` is `local`, which is the same guard
    // `prepare_sandbox_image` carries and the only thing that makes the grant
    // safe. They are granted at all because the folder is chosen from the
    // composer, which only exists in the workspace.
    let workspace = granted("workspace");
    assert!(workspace.contains(&"allow-open-control-center".to_string()));
    assert!(workspace.iter().all(|permission| {
        matches!(
            permission.as_str(),
            "allow-open-control-center"
                | "allow-discover-provider-models"
                | "allow-configure-ai-provider"
                | "allow-sandbox-image-status"
                | "allow-conversation-folder"
                | "allow-bind-conversation-folder"
                | "allow-unbind-conversation-folder"
        ) || permission.starts_with("allow-agent-host-")
    }));
    for forbidden in [
        "allow-apply-operator-config",
        "allow-sharing-action",
        "allow-prepare-runtime",
        "allow-repair-runtime",
        "allow-stop",
        "core:default",
    ] {
        assert!(
            !workspace.contains(&forbidden.to_string()),
            "the workspace origin must not be granted {forbidden}",
        );
    }

    let patterns: Vec<tauri::utils::acl::RemoteUrlPattern> = capability("workspace")["remote"]
        ["urls"]
        .as_array()
        .expect("remote urls")
        .iter()
        .map(|value| {
            value
                .as_str()
                .expect("url string")
                .parse()
                .expect("valid pattern")
        })
        .collect();
    let matches = |raw: &str| {
        let url = tauri::Url::parse(raw).expect("valid url");
        patterns.iter().any(|pattern| pattern.test(&url))
    };

    assert!(matches("http://app.lemma.localhost:3711/pod/abc"));
    assert!(matches("http://app.lemma.localhost:63844/"));
    assert!(matches("https://lemma.work/pod/abc"));

    // Sharing publishes the same workspace on a different host. Those
    // visitors must not be able to drive this Mac's Local settings.
    assert!(!matches("http://192.168.1.24:3711/"));
    assert!(!matches("https://team.trycloudflare.com/"));
    assert!(!matches("https://lemma.work.evil.example/"));
    assert!(!matches("http://lemma.work/"));
}

#[test]
fn an_overridden_workspace_origin_gets_the_same_commands() {
    let capability_for =
        |values: &[&str]| workspace_capability_for(values.iter().map(|value| value.to_string()));

    assert!(capability_for(&[]).is_none());
    // A shipped origin is already covered; re-granting it would only widen
    // the pattern set for no reason.
    assert!(capability_for(&["https://lemma.work"]).is_none());
    assert!(capability_for(&["not a url"]).is_none());

    // ...including a local workspace, whose port is not known until it is
    // allocated. The shipped entry is `http://app.<base>:*`, and the origin
    // checked against it is concrete, so a plain string comparison never
    // matched one and quietly minted an override for every local launch.
    for base in ["lemma.localhost", "127.0.0.1.sslip.io"] {
        assert!(
            capability_for(&[&format!("http://app.{base}:52413")]).is_none(),
            "the shipped capability already covers app.{base} on any port",
        );
    }
    // And the wildcard is the port alone. A host that merely starts the
    // same way is a different machine, and must still be treated as an
    // override rather than silently accepted as shipped.
    assert!(
        capability_for(&["http://app.lemma.localhost.evil:52413"]).is_some(),
        "a lookalike host must not read as a shipped origin",
    );
    assert!(!shipped_workspace_origin_covers(
        "http://app.lemma.localhost:*",
        "http://app.lemma.localhost:52413/admin"
    ));
    assert!(!shipped_workspace_origin_covers(
        "http://app.lemma.localhost:*",
        "http://app.lemma.localhost"
    ));

    let raw = capability_for(&["https://staging.lemma.work/", "http://127.0.0.1:3711"])
        .expect("an overridden origin produces a capability");
    let capability: Value = serde_json::from_str(&raw).expect("valid capability JSON");
    assert_eq!(
        capability["remote"]["urls"],
        json!(["https://staging.lemma.work", "http://127.0.0.1:3711"]),
    );
    // Read from the shipped file rather than restated here: a hardcoded copy
    // is exactly what drifted, and an assertion that has to be remembered
    // catches nothing.
    let shipped: Value =
        serde_json::from_str(SHIPPED_WORKSPACE_CAPABILITY).expect("valid shipped capability");
    assert_eq!(
        capability["permissions"], shipped["permissions"],
        "an override must reach neither further nor less far than the shipped capability",
    );
    // The one this drift actually cost, named so the regression reads as
    // the symptom it produced.
    assert!(
        capability["permissions"]
            .as_array()
            .expect("permissions are an array")
            .contains(&json!("allow-agent-host-status")),
        "a self-hosted workspace must be able to ask this computer for its status",
    );
    assert_eq!(capability["local"], json!(false));
}

/// Every local base this build serves has to be an origin the workspace
/// capability covers.
///
/// The failure this catches is silent and total. A workspace served on a
/// base the capability does not list matches no capability at all, so
/// opening Local settings, connecting the Agent Host and configuring a
/// provider each answer `not allowed by ACL` -- the whole of onboarding,
/// with nothing on screen to say why. Nothing else ties the two files
/// together: the base domain is chosen in locald and the origins are
/// declared in a Tauri capability, and neither imports the other.
#[test]
fn every_local_base_this_build_serves_is_a_shipped_workspace_origin() {
    let shipped = shipped_workspace_origins();
    for base in TRUSTED_LOCAL_BASES {
        let origin = format!("http://app.{base}:52413");
        assert!(
            shipped
                .iter()
                .any(|pattern| shipped_workspace_origin_covers(pattern, &origin)),
            "capabilities/workspace.json does not cover {origin}, so a                  workspace served there reaches no shell command at all",
        );
    }
}

#[test]
fn a_launch_tells_the_auth_portal_where_to_come_back_to() {
    // The portal auto-continues an existing session only when it is given
    // somewhere to go; without one it stops on a Continue button. Every
    // cold start reached it without one, because a new runtime generation
    // means the recorded resume target never matches.
    let url = local_auth_url_returning_to("http://app.lemma.localhost:3711/", "signup", "/");
    assert!(
        url.starts_with("http://app.lemma.localhost:3711/auth?"),
        "{url}"
    );
    assert!(url.contains("show=signup"), "{url}");
    assert!(
        url.contains("redirect_uri=%2F"),
        "the portal needs a return address to continue without asking: {url}"
    );

    // Relative, so it resolves against the portal's own origin and cannot
    // be aimed off it -- and so it survives locald allocating a different
    // port than the one this launch used.
    let sneaky = local_auth_url_returning_to(
        "http://app.lemma.localhost:3711",
        "signin",
        "https://evil.example",
    );
    assert!(sneaky.contains("redirect_uri=%2F"), "{sneaky}");
    assert!(!sneaky.contains("evil.example"), "{sneaky}");
}

#[test]
fn a_resume_opens_the_recorded_route_rather_than_the_workspace_root() {
    // Opening the root means loading the app once to authenticate and
    // resolve the last pod, then loading it again at the pod it resolved
    // to. The recorded route skips the first of those.
    let target = ResumeTarget {
        url: "http://app.lemma.localhost:49180".into(),
        api_url: "http://app.lemma.localhost:49181".into(),
        generation: "abc123".into(),
        release: env!("CARGO_PKG_VERSION").into(),
        route: "/pod/42/conversations/7".into(),
    };
    assert_eq!(
        resume_entry_url(&target),
        "http://app.lemma.localhost:49180/pod/42/conversations/7"
    );

    // An install that has only ever seen the root still resumes; it just
    // resumes at the root, without a doubled slash.
    let root = ResumeTarget {
        route: "/".into(),
        ..target
    };
    assert_eq!(resume_entry_url(&root), "http://app.lemma.localhost:49180");
}

#[test]
fn a_resume_target_is_only_trusted_with_a_generation_and_workspace_origins() {
    // The generation is what separates "our stack is still serving" from
    // "something is listening on that port". Without it, a resume would
    // hand the window to whatever answered.
    let saved = |generation: &str, url: &str| {
        json!({
            "resumeTarget": {
                "url": url,
                "apiUrl": "http://app.lemma.localhost:49181",
                "generation": generation,
                "route": "/",
            }
        })
    };
    let parse = |value: Value| -> bool {
        let entry = &value["resumeTarget"];
        let generation = entry["generation"].as_str().unwrap_or_default();
        !generation.is_empty()
            && trusted_workspace_urls(
                entry["url"].as_str().unwrap_or_default(),
                entry["apiUrl"].as_str().unwrap_or_default(),
            )
    };

    assert!(parse(saved("abc123", "http://app.lemma.localhost:49180")));
    assert!(!parse(saved("", "http://app.lemma.localhost:49180")));
    assert!(!parse(saved("abc123", "https://evil.example.com")));
}

#[test]
fn configured_origins_are_exact() {
    let same = tauri::Url::parse("https://lemma.work/docs").unwrap();
    let subdomain = tauri::Url::parse("https://untrusted.lemma.work/").unwrap();
    let wrong_port = tauri::Url::parse("http://localhost:9999/").unwrap();

    assert!(same_origin(&same, "https://lemma.work"));
    assert!(!same_origin(&subdomain, "https://lemma.work"));
    assert!(!same_origin(&wrong_port, "http://localhost:3711"));
}

#[test]
fn ordinary_web_navigation_stays_in_the_webview() {
    let urls = [
        "https://sales.apps.lemma.work/",
        "https://api.lemma.work/widgets/serve/conversation/tool",
        "http://sales.apps.lemma.localhost:8711/",
        "https://widgets.example.com/report",
    ];

    for raw_url in urls {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert_eq!(
            navigation_disposition(&url, "hosted", "https://lemma.work", ""),
            NavigationDisposition::Allow
        );
    }
}

#[test]
fn downloads_are_judged_by_the_origin_that_minted_them() {
    let app_base = "http://app.lemma.localhost:63844";
    let api_base = "http://app.lemma.localhost:63845";

    // Object URLs from the workspace itself: every Download button in the app.
    for raw_url in [
        "blob:http://app.lemma.localhost:63844/9f1c-uuid",
        "blob:http://app.lemma.localhost:63845/9f1c-uuid",
        // Bundle export navigates straight to a Content-Disposition endpoint.
        "http://app.lemma.localhost:63845/pods/demo/bundle/download",
    ] {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert!(
            download_disposition(&url, "local", app_base, api_base),
            "{raw_url} should download"
        );
    }

    for raw_url in [
        // A loopback origin this install does not own.
        "blob:http://app.lemma.localhost:3710/9f1c-uuid",
        "http://127.0.0.1:3000/export.csv",
        // Schemes that should never reach the disk by themselves.
        "file:///etc/passwd",
        "data:text/csv,a%2Cb",
        "blob:not-a-url",
    ] {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert!(
            !download_disposition(&url, "local", app_base, api_base),
            "{raw_url} should not download"
        );
    }
}

#[test]
fn desktop_frontend_launcher_has_no_shared_development_origin_fallback() {
    let launcher = include_str!("../../runtime/frontend-launcher.mjs").replace("\r\n", "\n");
    assert!(launcher.contains("locald must provide the isolated frontend and API origins"));
    assert!(!launcher.contains("app.lemma.localhost:3711"));
    assert!(!launcher.contains("app.lemma.localhost:8711"));
}

#[test]
fn unsupported_navigation_schemes_are_denied() {
    for raw_url in [
        "file:///tmp/report.html",
        "javascript:alert(1)",
        "lemma://other",
    ] {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert_eq!(
            navigation_disposition(&url, "hosted", "https://lemma.work", ""),
            NavigationDisposition::Deny
        );
    }
}

/// "Open in new window" on a pod app must not eat the window Lemma is in.
///
/// It used to answer `NavigateInApp`, which pointed the *main* webview at
/// the app: the workspace, the sidebar and every tab were replaced by
/// somebody's dashboard, and nothing on screen led back. The only exit was
/// quitting the app.
#[test]
fn a_pod_app_opens_beside_lemma_rather_than_on_top_of_it() {
    let api_base = "http://app.lemma.localhost:8711";
    let app_base = "http://app.lemma.localhost:3000";
    let pod_app = tauri::Url::parse("http://study-lab.apps.lemma.localhost:8711/").unwrap();

    assert_eq!(
        new_window_disposition(&pod_app, "local", app_base, api_base),
        NewWindowDisposition::OpenAppWindow
    );

    // The workspace itself still belongs in the main window -- this is a
    // rule about apps, not a general retreat from in-app navigation.
    let workspace = tauri::Url::parse("http://app.lemma.localhost:3000/pods").unwrap();
    assert_eq!(
        new_window_disposition(&workspace, "local", app_base, api_base),
        NewWindowDisposition::NavigateInApp
    );

    // And an app on a port that is not the API's is not our app: it must
    // not inherit a window that skips the browser policy.
    let impostor = tauri::Url::parse("http://study-lab.apps.lemma.localhost:9999/").unwrap();
    assert_ne!(
        new_window_disposition(&impostor, "local", app_base, api_base),
        NewWindowDisposition::OpenAppWindow
    );
}

#[test]
fn desktop_browser_login_is_explicitly_marked() {
    let desktop = tauri::Url::parse(
        "https://lemma.work/auth?desktop_browser=1&desktop_request=request-1234567890",
    )
    .unwrap();
    let ordinary = tauri::Url::parse("https://lemma.work/auth").unwrap();
    let unrelated = tauri::Url::parse("https://lemma.work/docs?desktop_browser=1").unwrap();

    assert!(is_desktop_browser_auth_url(&desktop));
    assert!(!is_desktop_browser_auth_url(&ordinary));
    assert!(!is_desktop_browser_auth_url(&unrelated));
    assert_eq!(
        navigation_disposition(&desktop, "hosted", "https://lemma.work", ""),
        NavigationDisposition::OpenExternal
    );
    assert_eq!(
        new_window_disposition(&desktop, "hosted", "https://lemma.work", ""),
        NewWindowDisposition::OpenExternal
    );
}

#[test]
fn local_settings_navigation_is_restricted_to_trusted_packaged_and_dev_assets() {
    assert!(control_navigation_allowed(
        &tauri::Url::parse(&native_assets::url("control.html", None)).unwrap()
    ));
    assert!(control_navigation_allowed(
        &tauri::Url::parse("http://127.0.0.1:1430/control.html").unwrap()
    ));
    assert!(!control_navigation_allowed(
        &tauri::Url::parse("http://127.0.0.1:1431/control.html").unwrap()
    ));
    assert!(!control_navigation_allowed(
        &tauri::Url::parse("http://127.0.0.1:1430/index.html").unwrap()
    ));
    assert!(!control_navigation_allowed(
        &tauri::Url::parse(&native_assets::url("index.html", None)).unwrap()
    ));
    assert!(!control_navigation_allowed(
        &tauri::Url::parse("https://example.com/control.html").unwrap()
    ));
}

#[test]
fn the_dev_asset_origin_is_still_refused_for_anything_but_bundled_pages() {
    // The development exception widens the trusted origin; it must not
    // widen what a *remote* page can reach.
    assert!(!trusted_native_asset_url(
        &tauri::Url::parse("http://127.0.0.1:3000/index.html").unwrap()
    ));
    assert!(!trusted_control_url(
        &tauri::Url::parse(&native_asset_url("index.html")).unwrap()
    ));
    assert!(trusted_control_url(
        &tauri::Url::parse(&native_asset_url("control.html")).unwrap()
    ));
}

#[test]
fn macos_allows_only_the_local_http_frontend_and_app_subdomains() {
    let plist = include_str!("../../Info.plist").replace("\r\n", "\n");

    assert!(plist.contains("NSAllowsLocalNetworking"));
    assert!(plist.contains("lemma.localhost"));
    assert!(plist.contains("NSIncludesSubdomains"));
    assert!(!plist.contains("NSAllowsArbitraryLoads"));
    assert!(!plist.contains("NSAllowsArbitraryLoadsInWebContent"));
}

#[test]
fn hosted_auth_uses_browser_handoff_while_local_auth_stays_in_app() {
    assert_eq!(
        desktop_auth_url("https://lemma.work", "signup"),
        "https://lemma.work/auth/desktop?mode=signup"
    );
    assert_eq!(
        local_auth_url("http://app.lemma.localhost:3711/", "signup"),
        "http://app.lemma.localhost:3711/auth?show=signup"
    );
}

#[test]
fn native_material_attributes_survive_navigation() {
    // Local mode navigates the main window from the splash to the
    // workspace. Anything set by a one-shot eval is gone by then, so both
    // attributes have to be written by the initialization script, which
    // Tauri re-runs for every document.
    let script = desktop_context_script("local");

    assert!(script.contains("setAttribute('data-desktop-platform', d.platform)"));
    assert!(script.contains("if (d.vibrancy) root.setAttribute('data-desktop-vibrancy'"));
    assert!(script.contains("if (d.systemAccent) root.style.setProperty('--accent-rgb'"));
}

/// A return route that starts with a slash is not automatically relative.
///
/// The portal hands the decoded `redirect_uri` to `window.location.replace`,
/// which resolves `//evil.example/x` against the *scheme* and not the origin
/// -- so a protocol-relative value leaves the portal entirely. Backslash is
/// the same door: browsers normalise `/\host` to `//host`.
#[test]
fn a_return_route_that_leaves_the_portal_is_replaced_with_the_root() {
    let base = "http://lemma.localhost:63844";
    for hostile in ["//evil.example/x", "/\\evil.example/x", "//evil.example"] {
        let url = local_auth_url_returning_to(base, "signin", hostile);
        assert!(
            url.contains("redirect_uri=%2F&") || url.ends_with("redirect_uri=%2F"),
            "{hostile} was kept as a return route: {url}"
        );
    }
    // An ordinary relative route still survives.
    let url = local_auth_url_returning_to(base, "signin", "/pods/abc");
    assert!(url.contains("redirect_uri=%2Fpods%2Fabc"), "{url}");
    // As does the fallback for anything not rooted at all.
    let url = local_auth_url_returning_to(base, "signin", "pods/abc");
    assert!(
        url.contains("redirect_uri=%2F&") || url.ends_with("redirect_uri=%2F"),
        "{url}"
    );
}

/// A launch must not depend on an environment variable being well formed.
#[test]
fn an_unparseable_hosted_url_opens_the_splash_rather_than_aborting_setup() {
    assert!(matches!(
        hosted_entry_url("https://lemma.example.com"),
        WebviewUrl::External(_)
    ));
    for malformed in ["", "not a url", "http://", ":://", "lemma.example.com"] {
        assert!(
            matches!(hosted_entry_url(malformed), WebviewUrl::App(_)),
            "{malformed:?} has to fall back to the splash, not abort setup"
        );
    }
}
