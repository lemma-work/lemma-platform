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
}

/// A pod app framed through its alias is on the workspace's host, one port
/// over. Same site, different origin -- and no command answers it.
#[test]
fn a_pod_app_alias_on_the_workspace_host_reaches_no_command() {
    let alias = url("http://app.lemma.localhost:61001/");
    assert!(!local_settings_origin_allowed("local", &alias, LOCAL, None));
    assert!(!agent_host_origin_allowed("local", &alias, LOCAL, None));
    // The capability that grants the workspace its commands names the
    // workspace's exact origin, so the alias matches no capability either.
    let capability: Value =
        serde_json::from_str(&local_workspace_capability(LOCAL).expect("a local capability"))
            .expect("valid capability JSON");
    let pattern: tauri::utils::acl::RemoteUrlPattern = capability["remote"]["urls"][0]
        .as_str()
        .expect("one url")
        .parse()
        .expect("a valid pattern");
    assert!(pattern.test(&url("http://app.lemma.localhost:52413/pods/1?x=2")));
    assert!(!pattern.test(&alias));
    assert!(!pattern.test(&url("http://orders.apps.lemma.localhost:52413/")));
}

#[test]
fn a_shared_origin_is_refused_even_while_the_app_points_at_it() {
    // Sharing moves the canonical origin, and the app's window with it. The
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
        "pub(crate) async fn test_server_setup(",
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
    let (request, consent) = workspace_sharing_request(
        "enable",
        Some(json!({"mode": "public", "provider": "ngrok", "public_warning_confirmed": true})),
        "invite_only",
        None,
    )
    .expect("a valid request");
    assert!(consent.is_some());
    assert_eq!(request["cmd"], "sharing.enable");
    assert_eq!(request["payload"]["public_warning_confirmed"], false);

    // The consent is set only after the native confirmation answers yes.
    let source = include_str!("../workspace_settings.rs").replace("\r\n", "\n");
    let body = function_body(&source, "pub(crate) fn consented_sharing_request(");
    let asked = body.find("consent.ask(app)?").expect("asks");
    let set = body
        .find("[\"public_warning_confirmed\"] = Value::Bool(true)")
        .expect("sets consent");
    assert!(asked < set);
}

/// Every change that lets somebody else in is asked natively, not only Public.
#[test]
fn sharing_on_the_local_network_and_opening_signup_are_asked_natively() {
    let (request, consent) = workspace_sharing_request(
        "enable",
        Some(json!({"mode": "local_network", "interface": "192.168.1.20", "public_warning_confirmed": true})),
        "invite_only",
        None,
    )
    .expect("a valid request");
    let consent = consent.expect("the local network is asked about");
    assert_eq!(request["payload"]["public_warning_confirmed"], false);
    assert!(consent.message.contains(local_join_sentence("invite_only")));

    let (_, consent) = workspace_sharing_request(
        "access",
        Some(json!({"who_can_join": "open"})),
        "invite_only",
        None,
    )
    .unwrap();
    assert!(consent.is_some(), "opening signup went unasked");
    // Narrowing who can join takes nothing from anybody.
    let (_, consent) = workspace_sharing_request(
        "access",
        Some(json!({"who_can_join": "invite_only"})),
        "open",
        None,
    )
    .unwrap();
    assert!(consent.is_none());
    for action in ["snapshot", "disable"] {
        assert!(workspace_sharing_request(action, None, "open", None)
            .unwrap()
            .1
            .is_none());
    }
}

/// The sentence agreed to is the policy enforced, read from the request.
///
/// The Public dialog was worded from the saved preference while the request
/// itself could carry `who_can_join: "open"`: the person read "only people you
/// invite", and the link went live with open signup.
#[test]
fn the_consent_describes_the_join_policy_the_request_carries() {
    let (request, consent) = workspace_sharing_request(
        "enable",
        Some(json!({"mode": "public", "provider": "ngrok", "who_can_join": "open"})),
        "invite_only",
        None,
    )
    .unwrap();
    assert_eq!(request["payload"]["who_can_join"], "open");
    assert!(consent
        .unwrap()
        .message
        .starts_with(public_join_sentence("open")));

    // Absent from the request: the saved policy is written into it, so the
    // daemon cannot apply anything but what was described.
    let (request, consent) = workspace_sharing_request(
        "enable",
        Some(json!({"mode": "public", "provider": "ngrok"})),
        "open",
        None,
    )
    .unwrap();
    assert_eq!(request["payload"]["who_can_join"], "open");
    assert!(consent
        .unwrap()
        .message
        .starts_with(public_join_sentence("open")));

    assert!(workspace_sharing_request(
        "enable",
        Some(json!({"mode": "public", "who_can_join": "everyone"})),
        "invite_only",
        None,
    )
    .is_err());
}

/// The shell restates locald's sentences; they must say the same thing.
#[test]
fn the_shells_join_sentences_are_the_daemons() {
    use lemma_locald::sharing::{local_join_warning, public_warning, WhoCanJoin};
    for (name, who) in [
        ("open", WhoCanJoin::Open),
        ("invite_only", WhoCanJoin::InviteOnly),
    ] {
        assert_eq!(public_join_sentence(name), public_warning(who));
        assert_eq!(local_join_sentence(name), local_join_warning(who));
    }
}

/// Local settings takes the same path, so the bundled page is no way around it.
#[test]
fn local_settings_sharing_goes_through_the_same_consent() {
    let source = include_str!("../operator_settings.rs").replace("\r\n", "\n");
    let body = function_body(&source, "pub(crate) fn sharing_action_impl(");
    assert!(body.contains("consented_sharing_request(&app, &action, payload, Some(id))?"));
    assert!(!body.contains("request[\"payload\"] = payload"));
}

#[test]
fn replacing_a_credential_is_asked_and_setting_a_first_one_is_not() {
    let operator = json!({
        "config": {"integrations": {"google_client_id": "ours.apps", "slack_client_id": ""}},
        "secrets": {"integrations.google_client_secret": true},
    });
    // First-time values: nothing of the person's is lost.
    let first = json!({
        "section": {"name": "integrations", "value": {"google_client_id": "ours.apps", "slack_client_id": "new"}},
        "secrets": {"integrations.slack_client_secret": {"action": "replace", "value": "s"}},
    });
    assert!(credential_replacements(&operator, &first).is_empty());
    assert!(credential_consent(&[]).is_none());

    let replacing = json!({
        "section": {"name": "integrations", "value": {"google_client_id": "theirs.apps"}},
        "secrets": {
            "integrations.google_client_secret": {"action": "replace", "value": "x"},
        },
    });
    assert_eq!(
        credential_replacements(&operator, &replacing),
        ["google client id", "google client secret"]
    );
    let removing = json!({
        "section": {"name": "integrations", "value": {"google_client_id": "ours.apps"}},
        "secrets": {"integrations.google_client_secret": {"action": "remove"}},
    });
    assert_eq!(
        credential_replacements(&operator, &removing),
        ["google client secret"]
    );

    let source = include_str!("../workspace_settings.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn apply_local_settings_impl(");
    let asked = body.find("consent.ask(&app)?").expect("asks");
    let applied = body.find("\"config.apply\"").expect("applies");
    assert!(asked < applied);
}

/// The consent is the one place a person learns what these commands can
/// reach, so it has to say what the Seatbelt profile actually allows: reads
/// are broad, and only writes are confined.
#[test]
fn the_host_execution_consent_says_reads_are_broad_and_writes_are_not() {
    let consent = host_execution_consent(true).expect("asks");
    assert!(
        consent.message.contains("read most files"),
        "{}",
        consent.message
    );
    assert!(consent.message.contains("SSH keys"), "{}", consent.message);
    assert!(
        consent.message.contains("write only"),
        "{}",
        consent.message
    );
    assert!(!consent.message.contains("to the folders you connect."));
}

#[test]
fn turning_host_execution_on_is_asked_and_turning_it_off_is_not() {
    assert!(host_execution_consent(true).is_some());
    assert!(host_execution_consent(false).is_none());
    let source = include_str!("../workspace_settings.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn set_host_execution_impl(");
    let asked = body.find("consent.ask(&app)?").expect("asks");
    let sent = body
        .find("agent_host_request(&app, host_execution_request(enabled))")
        .expect("sends");
    assert!(asked < sent);
}

#[test]
fn sharing_requests_are_limited_to_the_known_actions() {
    assert!(workspace_sharing_request("snapshot", None, "invite_only", None).is_ok());
    let (preflight, _) = workspace_sharing_request(
        "preflight",
        Some(json!({"provider": "cloudflare"})),
        "invite_only",
        None,
    )
    .unwrap();
    assert_eq!(preflight["provider"], "cloudflare");
    assert!(preflight.get("payload").is_none());
    for refused in ["reset", "sharing.enable", ""] {
        assert!(
            workspace_sharing_request(refused, None, "invite_only", None).is_err(),
            "{refused}"
        );
    }
    let (with_id, _) =
        workspace_sharing_request("snapshot", None, "invite_only", Some("control-7".into()))
            .unwrap();
    assert_eq!(with_id["id"], "control-7");
}

#[test]
fn a_page_on_a_host_that_stopped_resolving_to_this_mac_is_refused() {
    use std::net::{IpAddr, Ipv4Addr};
    let loopback = |_: &str| vec![IpAddr::V4(Ipv4Addr::LOCALHOST)];
    let hostile = |_: &str| vec![IpAddr::V4(Ipv4Addr::new(203, 0, 113, 5))];
    let mixed = |_: &str| {
        vec![
            IpAddr::V4(Ipv4Addr::LOCALHOST),
            IpAddr::V4(Ipv4Addr::new(203, 0, 113, 5)),
        ]
    };
    let nothing = |_: &str| Vec::new();
    // A development override can serve the workspace on a name that is not
    // `.localhost`; that one is asked about at the moment of the call.
    let named = url("http://app.lemma-dev.example:52413/t");
    assert!(page_host_is_loopback(&named, loopback));
    assert!(!page_host_is_loopback(&named, hostile));
    assert!(!page_host_is_loopback(&named, mixed));
    assert!(!page_host_is_loopback(&named, nothing));
    // `*.localhost` is loopback by convention and never asks a resolver.
    assert!(page_host_is_loopback(
        &url("http://app.lemma.localhost:52413/"),
        hostile
    ));
    assert!(page_host_is_loopback(
        &url("http://127.0.0.1:3000/"),
        hostile
    ));

    let source = include_str!("../workspace_settings.rs").replace("\r\n", "\n");
    let body = function_body(&source, "pub(crate) fn require_local_settings_caller(");
    assert!(body.contains("page_host_is_loopback(&page, resolve_host)"));
}

#[test]
fn the_workspace_writes_the_server_setup_sections_only() {
    for name in ["integrations", "surfaces", "ai", "email"] {
        assert!(workspace_section_allowed(&json!({"section": {"name": name}})).is_ok());
    }
    assert!(workspace_section_allowed(&json!({"section": {"name": "sharing"}})).is_err());
    assert!(workspace_section_allowed(&json!({})).is_err());
}

#[test]
fn replacing_the_ai_key_is_asked_and_choosing_a_model_is_not() {
    let operator = json!({
        "config": {"ai": {"default_model": "big", "fast_model": ""}},
        "secrets": {"ai.api_key": true},
    });
    let choosing = json!({
        "section": {"name": "ai", "value": {"default_model": "other", "fast_model": "quick"}},
        "secrets": {},
    });
    assert!(credential_replacements(&operator, &choosing).is_empty());
    let rekeying = json!({
        "section": {"name": "ai", "value": {"default_model": "big"}},
        "secrets": {"ai.api_key": {"action": "replace", "value": "k"}},
    });
    assert_eq!(credential_replacements(&operator, &rekeying), ["api key"]);
    let unsetting = json!({
        "section": {"name": "email", "value": {"from_email": "new@example.com"}},
        "secrets": {"email.smtp_password": {"action": "remove"}},
    });
    let operator = json!({
        "config": {"email": {"from_email": "old@example.com"}},
        "secrets": {"email.smtp_password": true},
    });
    assert_eq!(
        credential_replacements(&operator, &unsetting),
        ["smtp password"]
    );
}

#[test]
fn a_setup_test_forwards_only_what_a_test_takes() {
    let request = setup_test_request(&json!({
        "service": "telegram",
        "credential": "1:abc",
        "from_email": null,
        "install_id": "not forwarded",
    }))
    .unwrap();
    assert_eq!(request["cmd"], "config.test");
    assert_eq!(
        request["payload"],
        json!({"service": "telegram", "credential": "1:abc"})
    );
    assert!(setup_test_request(&json!({})).is_err());
}

#[test]
fn the_setup_test_is_granted_to_the_workspace_and_registered() {
    let capability = include_str!("../../capabilities/workspace.json").replace("\r\n", "\n");
    assert!(capability.contains("\"allow-test-server-setup\""));
    let app = include_str!("../app.rs").replace("\r\n", "\n");
    assert!(app.contains("workspace_settings::test_server_setup"));
    let build = include_str!("../../build.rs").replace("\r\n", "\n");
    assert!(build.contains("\"test_server_setup\""));
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
