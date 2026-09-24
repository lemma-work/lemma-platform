use super::*;

fn url(raw: &str) -> tauri::Url {
    tauri::Url::parse(raw).expect("valid url")
}

const LOCAL: &str = "http://app.lemma.localhost:52413/";

#[test]
fn the_local_workspace_reaches_this_computers_settings() {
    assert!(local_settings_origin_allowed(
        "local",
        &url("http://app.lemma.localhost:52413/t?settings=this-mac"),
        LOCAL,
        None,
    ));
    // The loopback wildcard base is as much this install as the other one.
    assert!(local_settings_origin_allowed(
        "local",
        &url("http://app.127.0.0.1.sslip.io:52413/"),
        "http://app.127.0.0.1.sslip.io:52413/",
        None,
    ));
}

#[test]
fn a_shared_origin_is_refused_even_while_the_app_points_at_it() {
    // Sharing moves the canonical origin, and the owner's window with it. The
    // workspace URL then *is* the shared one, so matching it alone would hand
    // this computer's settings to the address every visitor loads.
    for shared in [
        "http://192.168.1.20:61234/",
        "https://example.ngrok.app/",
        "https://lemma.example.com/",
    ] {
        assert!(
            !local_settings_origin_allowed("local", &url(shared), shared, None),
            "{shared} was allowed",
        );
    }
    // And a page on the loopback origin while the install is shared is not
    // the workspace the app is showing.
    assert!(!local_settings_origin_allowed(
        "local",
        &url(LOCAL),
        "https://example.ngrok.app/",
        None
    ));
}

#[test]
fn the_hosted_site_and_lookalikes_are_refused() {
    assert!(!local_settings_origin_allowed(
        "hosted",
        &url("https://lemma.work/"),
        "https://lemma.work/",
        None,
    ));
    // Local mode, but not the origin this app navigated to.
    assert!(!local_settings_origin_allowed(
        "local",
        &url("http://app.lemma.localhost:40000/"),
        LOCAL,
        None,
    ));
    assert!(!local_settings_origin_allowed(
        "local",
        &url("http://evil.lemma.localhost:52413/"),
        "http://evil.lemma.localhost:52413/",
        None,
    ));
    // https on a loopback host is not what locald serves.
    assert!(!local_settings_origin_allowed(
        "local",
        &url("https://app.lemma.localhost:52413/"),
        "https://app.lemma.localhost:52413/",
        None,
    ));
    // Hosted mode refuses even the loopback origin: there is no stack here.
    assert!(!local_settings_origin_allowed(
        "hosted",
        &url(LOCAL),
        LOCAL,
        None
    ));
}

#[test]
fn only_the_development_override_widens_the_rule() {
    let dev = "http://localhost:3000";
    let page = url("http://localhost:3000/t");
    assert!(local_settings_origin_allowed(
        "local",
        &page,
        "http://localhost:3000/",
        Some(dev)
    ));
    assert!(!local_settings_origin_allowed(
        "local",
        &page,
        "http://localhost:3000/",
        None
    ));
}

#[test]
fn every_this_mac_command_checks_its_caller_first() {
    // Being granted to the workspace capability is the first gate; the
    // capability also lists the hosted site. The Rust check is the second,
    // and a command without it would answer lemma.work.
    let source = include_str!("../workspace_settings.rs").replace("\r\n", "\n");
    for command in [
        "pub(crate) async fn local_settings_snapshot(",
        "pub(crate) async fn apply_local_settings(",
        "pub(crate) async fn local_sharing(",
        "pub(crate) async fn set_start_at_login(",
        "pub(crate) async fn set_host_execution(",
    ] {
        let body = function_body(&source, command);
        assert!(
            body.contains("require_local_settings_caller(&window, &app)?;"),
            "{command} does not check its caller",
        );
    }
    // The existing commands the workspace now shares with Local settings.
    for (file, command, guard) in [
        (
            include_str!("../app_update.rs"),
            "pub(crate) async fn check_for_app_update(",
            "require_settings_caller(&window, &app)?;",
        ),
        (
            include_str!("../app_update.rs"),
            "pub(crate) async fn install_app_update(",
            "require_settings_caller(&window, &app)?;",
        ),
        (
            include_str!("../telemetry.rs"),
            "pub(crate) fn telemetry_status(",
            "require_settings_caller(&window, &app)?;",
        ),
        (
            include_str!("../telemetry.rs"),
            "pub(crate) fn set_telemetry_enabled(",
            "require_settings_caller(&window, &app)?;",
        ),
        (
            include_str!("../runtime_setup.rs"),
            "pub(crate) async fn repair_runtime(",
            "require_settings_caller(&window, &app)?;",
        ),
        (
            include_str!("../operator_settings.rs"),
            "pub(crate) async fn prepare_sandbox_image(",
            "require_local_settings_caller(&window, &app)?;",
        ),
        (
            include_str!("../stack_control.rs"),
            "pub(crate) fn open_logs(",
            "require_local_settings_caller(&window, &app)?;",
        ),
        (
            include_str!("../diagnostics.rs"),
            "pub(crate) fn diagnostic_logs(",
            "require_local_settings_caller(&window, &app)?;",
        ),
    ] {
        let file = file.replace("\r\n", "\n");
        let body = function_body(&file, command);
        assert!(body.contains(guard), "{command} does not check its caller");
    }
}

#[test]
fn repair_from_the_workspace_is_asked_natively() {
    // The repair stops the stack serving the page that asked for it. The
    // question is the shell's, so a page cannot skip it.
    let source = include_str!("../runtime_setup.rs").replace("\r\n", "\n");
    let body = function_body(&source, "pub(crate) async fn repair_runtime(");
    let ask = body.find("confirm_destructive_action_impl").expect("asks");
    let repair = body.find("repair_runtime_impl(app)").expect("repairs");
    assert!(ask < repair);
}

#[test]
fn the_page_cannot_agree_to_a_public_link_on_its_own_behalf() {
    let (request, needs_consent) = workspace_sharing_request(
        "enable",
        Some(json!({"mode": "public", "provider": "ngrok", "public_warning_confirmed": true})),
    )
    .expect("a valid request");
    assert!(needs_consent);
    assert_eq!(request["cmd"], "sharing.enable");
    assert_eq!(request["payload"]["public_warning_confirmed"], false);

    // The local network needs no consent dialog, and gets none either way.
    let (request, needs_consent) = workspace_sharing_request(
        "enable",
        Some(json!({"mode": "local_network", "interface": "192.168.1.20", "public_warning_confirmed": true})),
    )
    .expect("a valid request");
    assert!(!needs_consent);
    assert_eq!(request["payload"]["public_warning_confirmed"], false);

    // The consent is set only after the native confirmation answers yes.
    let source = include_str!("../workspace_settings.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn local_sharing_impl(");
    let asked = body.find("confirm_destructive_action_impl").expect("asks");
    let set = body
        .find("[\"public_warning_confirmed\"] = Value::Bool(true)")
        .expect("sets consent");
    assert!(asked < set);
}

#[test]
fn sharing_requests_are_limited_to_the_known_actions() {
    assert!(workspace_sharing_request("snapshot", None).is_ok());
    let (preflight, _) =
        workspace_sharing_request("preflight", Some(json!({"provider": "cloudflare"}))).unwrap();
    assert_eq!(preflight["provider"], "cloudflare");
    assert!(preflight.get("payload").is_none());
    for refused in ["reset", "sharing.enable", ""] {
        assert!(
            workspace_sharing_request(refused, None).is_err(),
            "{refused}"
        );
    }
}

#[test]
fn the_workspace_writes_integrations_and_channels_only() {
    assert!(workspace_section_allowed(&json!({"section": {"name": "integrations"}})).is_ok());
    assert!(workspace_section_allowed(&json!({"section": {"name": "surfaces"}})).is_ok());
    assert!(workspace_section_allowed(&json!({"section": {"name": "ai"}})).is_err());
    assert!(workspace_section_allowed(&json!({})).is_err());
}

#[test]
fn the_settings_view_is_an_allowlist() {
    let view = workspace_settings_view(&json!({
        "release": "0.8.0",
        "schema": {"huge": true},
        "config_operations": {"x": {}},
        "state": {"ready": true, "running": true, "status": "Ready", "operation_id": "secret-ish"},
        "services": [{"id": "backend", "running": true, "pid": 42, "command": "python -m app"}],
        "operator": {
            "schema": {},
            "config": {"install_id": "abc", "revision": 3, "ai": {}, "integrations": {}, "surfaces": {}},
            "secrets": {"integrations.google_client_secret": true},
            "readiness": {"ai": "ready"},
        },
        "sharing": {"mode": "this_computer"},
    }));
    assert_eq!(view["release"], "0.8.0");
    assert!(view.get("schema").is_none());
    assert!(view.get("config_operations").is_none());
    assert!(view["state"].get("operation_id").is_none());
    assert!(view["services"][0].get("pid").is_none());
    assert!(view["services"][0].get("command").is_none());
    assert!(view["operator"]["config"].get("install_id").is_none());
    assert_eq!(view["operator"]["config"]["revision"], 3);
    // Presence, never a value -- which is all the daemon sends to begin with.
    assert_eq!(
        view["operator"]["secrets"]["integrations.google_client_secret"],
        true
    );
}

#[test]
fn the_menu_opens_settings_in_the_workspace_only_when_it_can_answer() {
    let page = url("http://app.lemma.localhost:52413/t");
    assert_eq!(
        settings_destination("local", true, false, Some(&page), LOCAL, None),
        SettingsDestination::Workspace,
    );
    // Starting, broken, hosted, on the splash, or moved to a shared origin:
    // Local settings, which is the page that exists for those cases.
    assert_eq!(
        settings_destination("local", false, false, Some(&page), LOCAL, None),
        SettingsDestination::Native,
    );
    assert_eq!(
        settings_destination("local", true, true, Some(&page), LOCAL, None),
        SettingsDestination::Native,
    );
    assert_eq!(
        settings_destination(
            "hosted",
            true,
            false,
            Some(&url("https://lemma.work/")),
            "https://lemma.work/",
            None
        ),
        SettingsDestination::Native,
    );
    assert_eq!(
        settings_destination(
            "local",
            true,
            false,
            Some(&url("tauri://localhost/index.html")),
            LOCAL,
            None
        ),
        SettingsDestination::Native,
    );
    let shared = "https://example.ngrok.app/";
    assert_eq!(
        settings_destination("local", true, false, Some(&url(shared)), shared, None),
        SettingsDestination::Native,
    );
    assert_eq!(
        settings_destination("local", true, false, None, LOCAL, None),
        SettingsDestination::Native,
    );
}

#[test]
fn the_open_settings_script_serialises_its_section() {
    let script = open_settings_script("this-mac-sharing");
    assert!(script.contains("\"lemma:open-settings\""));
    assert!(script.contains("section: \"this-mac-sharing\""));
    // A section is never spliced in raw.
    let hostile = open_settings_script("\"}));alert(1);//");
    assert!(
        hostile.contains(r#"section: "\"}));alert(1);//""#),
        "{hostile}"
    );
}

#[test]
fn the_settings_menu_items_go_through_the_fallback() {
    let menus = include_str!("../menus.rs").replace("\r\n", "\n");
    assert!(menus.contains(
        "\"control\" => {\n            open_settings(&app, \"this-mac\", \"overview\");"
    ));
    assert!(menus.contains("open_settings(&app, \"this-mac-sharing\", \"overview\")"));
}

#[test]
fn pages_that_moved_land_on_overview_rather_than_an_error() {
    for moved in [
        "ai",
        "sharing",
        "integrations",
        "channels",
        "runtime",
        "updates",
        "connectors",
        "surfaces",
        "services",
    ] {
        assert_eq!(
            control_center_page(Some(moved)).unwrap(),
            "overview",
            "{moved}"
        );
    }
    for kept in ["overview", "computer", "recovery", "diagnostics"] {
        assert_eq!(control_center_page(Some(kept)).unwrap(), kept);
    }
    assert!(control_center_page(Some("nonsense")).is_err());
}

#[test]
fn turning_host_execution_on_sends_the_daemon_a_boolean_and_nothing_else() {
    // The page picks on or off. Which folders are writable, the profile and
    // the grants are the host's to decide, so none of them can ride along.
    for enabled in [true, false] {
        let request = host_execution_request(enabled);
        assert_eq!(request["cmd"], "agent-host.host-execution");
        assert_eq!(request["enabled"], enabled);
        let mut keys: Vec<_> = request.as_object().unwrap().keys().cloned().collect();
        keys.sort();
        assert_eq!(keys, ["cmd", "enabled", "id"]);
    }
}

#[test]
fn host_execution_is_granted_to_the_workspace_and_registered() {
    let capability = include_str!("../../capabilities/workspace.json").replace("\r\n", "\n");
    assert!(capability.contains("\"allow-set-host-execution\""));
    let app = include_str!("../app.rs").replace("\r\n", "\n");
    assert!(app.contains("workspace_settings::set_host_execution"));
    let build = include_str!("../../build.rs").replace("\r\n", "\n");
    assert!(build.contains("\"set_host_execution\""));
}
