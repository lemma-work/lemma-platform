use super::*;

#[test]
fn every_command_that_is_not_pure_ui_is_async() {
    // A synchronous `#[tauri::command]` is dispatched on the main thread, so
    // one that waits on the daemon, the network or a child process freezes
    // every window for its whole duration. That is what made a first launch
    // a black, unresponsive app for minutes.
    //
    // Derived from the handler list rather than a list of names, because the
    // version of this test that named eight commands could not see the
    // twenty that were still blocking. Adding a synchronous command is now a
    // decision someone has to write down here, not an omission.
    //
    // The allowlist is only for commands that do nothing but touch UI, which
    // must stay on the main thread.
    const PURE_UI: &[&str] = &[
        "open_developer_tools",
        "close_local_settings",
        "get_state",
        // One mutex read of state locald has already pushed into the
        // shell. It talks to nothing, so dispatching it on the main
        // thread costs the lock and nothing else -- and it is polled
        // while a download runs, which is exactly when a command that
        // waited on the daemon would be felt.
        "sandbox_image_status",
    ];

    // Normalised, because the Windows runner checks out CRLF and the
    // patterns below are written with \n -- which is how this test passed on
    // macOS and failed on Windows against identical source.
    let source = shell_source();
    let source = source.as_str();
    let handlers = {
        let start = source
            .find("tauri::generate_handler![")
            .expect("the handler list exists");
        let end = source[start..].find("])").expect("the handler list closes") + start;
        &source[start..end]
    };
    // The handler list names each command by its module path now, and the
    // property below is about the function, not where it lives.
    let registered: Vec<&str> = handlers
        .lines()
        .skip(1)
        .map(|line| line.trim().trim_end_matches(','))
        .filter(|name| !name.is_empty() && !name.starts_with("//"))
        .map(|name| name.rsplit("::").next().expect("a command name"))
        .collect();
    assert!(
        registered.len() > 20,
        "parsed only {} commands, so this test is not reading the handler list",
        registered.len()
    );

    for name in registered {
        if PURE_UI.contains(&name) {
            assert!(
                source.contains(&format!("\nfn {name}("))
                    || source.contains(&format!("\npub(crate) fn {name}(")),
                "{name} is on the pure-UI allowlist but is async; either it \
                 waits on something and should come off the list, or the list \
                 is stale"
            );
            continue;
        }
        assert!(
            source.contains(&format!("async fn {name}("))
                || source.contains(&format!("#[tauri::command(async)]\nfn {name}("))
                // `pub(crate)` since the shell became modules; the property
                // this asserts -- dispatched off the main thread -- is the
                // attribute, not the visibility.
                || source.contains(&format!(
                    "#[tauri::command(async)]\npub(crate) fn {name}("
                )),
            "{name} is dispatched on the main thread. Either make it an async \
             command that hands its work to spawn_blocking, or add it to \
             PURE_UI with a reason."
        );
    }
}

#[test]
fn integrations_and_channels_moved_out_of_local_settings() {
    // The OAuth apps and bot credentials connectors and channels run with are
    // configured where they are needed: This Mac → Advanced in the workspace,
    // which the Connectors and Channels screens link to at the right form.
    // Local settings keeping a copy would be two forms for one secret.
    let markup = include_str!("../../ui/control.html").replace("\r\n", "\n");
    assert!(!markup.contains("data-page=\"integrations\""));
    assert!(!markup.contains("data-page=\"channels\""));
    assert!(!markup.contains("data-secret="));
}

#[test]
fn local_settings_never_gates_a_button_on_a_webview_confirm() {
    // WKWebView routes window.confirm() through a WKUIDelegate panel wry
    // does not implement, so it returns false without drawing anything:
    // the click is received and discarded, and the button looks inert.
    // Destructive actions go through the native dialog command instead.
    let script = CONTROL.replace("\r\n", "\n");
    assert!(
        !script.contains("window.confirm("),
        "a destructive button is gated on a confirm() that always says no"
    );
    assert!(
        script.contains("confirm_destructive_action"),
        "the native confirmation command is how those buttons ask"
    );
}

#[test]
fn every_command_is_granted_to_exactly_the_surfaces_that_call_it() {
    // Declaring an app manifest means an ungranted command is rejected at
    // runtime, from the bundled pages too. Nothing in the build surfaces
    // that - the page just stops working - so pin it here instead.
    let commands = include_str!("../../build.rs").replace("\r\n", "\n");
    let all: Vec<String> = commands
        .lines()
        .filter_map(|line| {
            line.trim()
                .strip_prefix('"')?
                .strip_suffix("\",")
                .map(str::to_string)
        })
        .collect();
    assert!(
        all.len() > 15,
        "failed to parse the command list from build.rs"
    );

    let mut all_granted: Vec<String> = Vec::new();
    for name in ["main", "control", "workspace", "confirmation"] {
        all_granted.extend(granted(name));
    }
    for command in &all {
        let permission = format!("allow-{}", command.replace('_', "-"));
        assert!(
            all_granted.contains(&permission),
            "{command} is registered but no capability grants {permission}, so every call to it is rejected",
        );
    }

    // Granted *somewhere* is not enough: a capability names one webview, so
    // a page calling a command only its sibling was granted is still
    // rejected at runtime. Check each bundled page against its own grants.
    for (capability, script) in [
        ("main", SPLASH),
        ("control", CONTROL),
        ("confirmation", include_str!("../../ui/confirmation.js")),
    ] {
        let grants = granted(capability);
        for command in invoked_commands(script) {
            let permission = format!("allow-{}", command.replace('_', "-"));
            assert!(
                grants.contains(&permission),
                "{capability} calls {command} but its capability lacks {permission}",
            );
        }
        // And the other direction, which nothing checked. A grant outlives the
        // code that needed it: the control window could still reach
        // `configure_ai_provider` and `local_recovery_options` long after its
        // own UI stopped calling either, and `installer_log` was granted to a
        // page that had never called it at all -- a second door to a file
        // `diagnostic_logs` already serves. None of that is exploitable on its
        // own; it is the surface being wider than the thing it exists for,
        // which is how the next one stops being noticed.
        let invoked = invoked_commands(script);
        for permission in grants.iter().filter(|grant| grant.starts_with("allow-")) {
            let command = permission.trim_start_matches("allow-").replace('-', "_");
            assert!(
                invoked.contains(&command),
                "{capability} is granted {permission} and never calls {command}; \
                 remove the grant, or the command with it",
            );
        }
    }
}

#[test]
fn a_sidecar_that_cannot_start_does_not_say_it_is_starting() {
    // "starting…" replaced "off" because nothing can switch this computer
    // off any more — but it is a promise about what happens next, and the
    // supervisor arms a restart backoff on every failed spawn. A sidecar
    // that cannot start at all said "starting…" for as long as the app was
    // open, with nothing anywhere to contradict it. Same shape as an adapter
    // stuck at "Setting up": a failure wearing the clothes of progress.
    assert_eq!(
        agent_host_tray_label(true, false, true, false, false, true),
        "Agent Host: not starting — see log",
    );
    // A failure recorded against a host that is up is about its workspaces,
    // not its startup, and the reachability states already say that better.
    assert_eq!(
        agent_host_tray_label(true, true, true, true, false, true),
        "Agent Host: connected",
    );
    assert_eq!(
        agent_host_tray_label(true, true, true, false, true, true),
        "Agent Host: workspace unreachable",
    );
    // And a build with no sidecar has nothing to fail.
    assert_eq!(
        agent_host_tray_label(false, false, false, false, false, true),
        "Agent Host: not installed",
    );
}

#[test]
fn local_settings_declares_no_palette_of_its_own() {
    // Local settings is a separate webview for a security reason — the
    // privileged commands are granted only to it — but that never justified
    // a second visual identity, which is what this file had: its own gold
    // accent, its own paper, its own display face. Colours now live in one
    // token block at the top and every rule reads from it, so this catches
    // the next raw hex before it becomes a third design system.
    let css = include_str!("../../ui/control.css").replace("\r\n", "\n");
    let rules = css
        .split_once("* { box-sizing: border-box; }")
        .expect("control.css starts with a token block then its rules")
        .1;

    let mut stray: Vec<&str> = Vec::new();
    for (index, _) in rules.match_indices('#') {
        let literal: String = rules[index..]
            .chars()
            .take_while(|character| character.is_ascii_hexdigit() || *character == '#')
            .collect();
        // A CSS id selector is not a colour; a colour is 3, 4, 6, or 8
        // hex digits and nothing else.
        if matches!(literal.len(), 4 | 5 | 7 | 9) && literal != "#ffffff" {
            stray.push(&rules[index..index + literal.len()]);
        }
    }
    assert!(
        stray.is_empty(),
        "control.css rules must read colours from the token block, found: {stray:?}"
    );

    // And the tokens themselves must be the product's, not a parallel set.
    assert!(
        css.contains("--accent-rgb: 90 63 212"),
        "light accent is the action violet"
    );
    assert!(
        css.contains("--accent-rgb: 139 122 245"),
        "dark accent is the action violet"
    );
    assert!(
        css.contains("--canvas: #f2efe7"),
        "light canvas is the product's paper"
    );
    assert!(
        !css.contains("Bricolage"),
        "the page no longer carries its own display face"
    );
}

#[test]
fn waiting_returns_as_soon_as_the_label_comes_free() {
    // The event loop needs a moment after `destroy`, not a fixed delay --
    // so this must return on the first poll that sees the label gone, and
    // must not have given up before then.
    let polls = std::cell::Cell::new(0);
    let released = wait_until_label_released(
        || {
            polls.set(polls.get() + 1);
            polls.get() < 3
        },
        Duration::from_secs(5),
        Duration::from_millis(1),
    );

    assert!(released);
    assert_eq!(polls.get(), 3, "it stopped polling the moment it came free");
}

#[test]
fn waiting_gives_up_rather_than_hanging_the_switch() {
    // A label that never frees must not park a blocking thread forever.
    // The caller logs and tries anyway; what it must not do is never return.
    let start = Instant::now();
    let released =
        wait_until_label_released(|| true, Duration::from_millis(30), Duration::from_millis(1));

    assert!(!released);
    assert!(start.elapsed() >= Duration::from_millis(30));
    assert!(
        start.elapsed() < Duration::from_secs(5),
        "the timeout has to actually bound the wait"
    );
}

/// The dialogs name the machine this build actually runs on.
#[test]
fn native_copy_does_not_name_the_wrong_hardware() {
    assert_eq!(
        THIS_COMPUTER,
        if cfg!(target_os = "windows") {
            "this PC"
        } else if cfg!(target_os = "macos") {
            "this Mac"
        } else {
            "this computer"
        }
    );
    let body = quit_prompt_body(&["Schedules stop.".into()]);
    assert!(body.contains(THIS_COMPUTER), "{body}");

    // The two highest-stakes strings in the app -- what is about to be
    // deleted -- were the ones this test did not reach. Both shipped in the
    // Windows build naming hardware that build's users do not have.
    let source = shell_source();
    for name in ["fn reset_local_data_impl(", "fn reset_full_reinstall_impl("] {
        let start = source.find(name).unwrap_or_else(|| panic!("{name} exists"));
        let body = head(&source[start..], 1200);
        let body = body.as_str();
        assert!(
            !body.contains("this Mac"),
            "{name} hardcodes 'this Mac' in the copy that names what is deleted",
        );
        assert!(
            body.contains("THIS_COMPUTER"),
            "{name} must name the machine this build actually runs on",
        );
    }
    // The other half of the bargain is stated where the decision is made.
    assert!(
        body.contains("To leave Lemma running, close the window instead."),
        "{body}"
    );
}

/// A web inspector does not ship enabled in the top-level menus.
#[test]
fn developer_tools_are_a_development_build_affordance() {
    let source = shell_source();
    for block in source.split("\"devtools\",").skip(1) {
        let head = head(block, 200);
        let head = head.as_str();
        assert!(
            head.contains("cfg!(debug_assertions)"),
            "a release build must not offer a web inspector into a webview \
             that talks to the workspace over IPC: {head}",
        );
    }
}

#[test]
fn iframes_may_render_their_own_inline_content() {
    let app_base = "http://app.lemma.localhost:63844";
    let api_base = "http://app.lemma.localhost:63845";

    // The document viewer previews HTML and .docx through `srcdoc`, and the
    // navigation delegate is asked about subframes too. Denying these blanked
    // the preview.
    for raw_url in ["about:srcdoc", "about:blank"] {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert_eq!(
            navigation_disposition(&url, "local", app_base, api_base),
            NavigationDisposition::Allow
        );
    }

    // Nothing else off the http/https path comes along for the ride.
    for raw_url in [
        "data:text/html,<h1>x</h1>",
        "file:///etc/passwd",
        "about:settings",
        "javascript:alert(1)",
    ] {
        let url = tauri::Url::parse(raw_url).unwrap();
        assert_eq!(
            navigation_disposition(&url, "local", app_base, api_base),
            NavigationDisposition::Deny
        );
    }
}

#[test]
fn local_settings_can_always_turn_sharing_off_and_nothing_else() {
    // Choosing a mode moved to This Mac → Sharing. Turning it off stays here:
    // a shared workspace moves this window to the shared origin, where the
    // workspace cannot reach this computer's settings, so this page is the
    // one that always can.
    let html = include_str!("../../ui/control.html").replace("\r\n", "\n");
    let script = CONTROL.replace("\r\n", "\n");
    assert!(html.contains("id=\"sharing-disable\""));
    assert!(script.contains("action: \"disable\""));
    assert!(!script.contains("action: \"enable\""));
    assert!(!html.contains("cloudflare-setup"));
}

#[test]
fn local_desktop_context_disables_email_verification_before_page_scripts() {
    let local = desktop_context_script("local");
    let hosted = desktop_context_script("hosted");

    assert!(local.contains("AUTH_EMAIL_VERIFICATION_REQUIRED: \"false\""));
    assert!(!hosted.contains("AUTH_EMAIL_VERIFICATION_REQUIRED"));
}

/// One panic on a background thread must not become a crash on the next lock.
///
/// `lock().unwrap()` was the shell's idiom at fifty-one sites. A thread that
/// panicked while holding `shell.ui` poisoned it, and the next menu refresh,
/// tray update or quit then panicked too.
#[test]
fn a_lock_poisoned_by_a_panic_is_still_usable() {
    use super::super::LockOrRecover;
    use std::sync::{Arc, Mutex};

    let shared = Arc::new(Mutex::new(1));
    let poisoner = Arc::clone(&shared);
    let _ = std::thread::spawn(move || {
        let mut value = poisoner.lock().unwrap();
        *value = 2;
        panic!("a background thread failed while holding the lock");
    })
    .join();

    assert!(shared.is_poisoned(), "the setup did not poison the lock");
    assert_eq!(
        *shared.lock_or_recover(),
        2,
        "the last value written is kept"
    );
    *shared.lock_or_recover() = 3;
    assert_eq!(*shared.lock_or_recover(), 3);
}

/// `CONTROL` lists its modules by hand, because `include_str!` needs literal
/// paths. A module added to `ui/control/` and not to that list would be
/// invisible to every guard that reads Local settings.
#[test]
fn every_control_module_is_read_by_the_guards() {
    let directory = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("ui/control");
    let mut on_disk: Vec<String> = std::fs::read_dir(&directory)
        .expect("ui/control exists")
        .filter_map(|entry| entry.ok()?.file_name().into_string().ok())
        .filter(|name| name.ends_with(".js"))
        .collect();
    on_disk.sort();
    let mut listed: Vec<String> = CONTROL_MODULES
        .iter()
        .map(|name| (*name).to_owned())
        .collect();
    listed.sort();
    assert_eq!(
        on_disk, listed,
        "add the new module to CONTROL in src/tests/mod.rs"
    );
}

/// Reset Data is offered from Recovery, so it has to work from Recovery.
///
/// `ensure_locald` refuses while Recovery pauses services, and the reset went
/// through it unchanged -- after it had already cleared the session. Asserted
/// on the source because the path needs a live AppHandle and a daemon.
#[test]
fn reset_data_leaves_recovery_and_signs_out_only_once_the_reset_is_accepted() {
    let source = include_str!("../local_recovery.rs").replace("\r\n", "\n");
    let body = function_body(&source, "pub(crate) fn reset_local_data_impl(");
    let leave = body
        .find("recovery_mode.swap(false")
        .expect("the reset leaves Recovery before it needs the daemon");
    let ensure = body
        .find("ensure_locald(&app)")
        .expect("the reset starts the daemon");
    let send = body
        .find("\"local.reset-data\"")
        .expect("the reset is sent");
    let clear = body
        .find("clear_local_session_data(&app)")
        .expect("the session is still cleared");
    assert!(leave < ensure, "{body}");
    assert!(
        send < clear,
        "the session must survive a reset that never started: {body}"
    );
    assert!(
        body.contains("recovery_mode.store(true"),
        "a reset that cannot start must put Recovery back: {body}"
    );
}

/// locald outlives the app by design, so it must not share the app's process
/// group: launchd reaps a LaunchAgent job's whole group when the app exits.
#[test]
fn the_daemon_is_spawned_into_its_own_process_group() {
    let source = include_str!("../locald_process.rs").replace("\r\n", "\n");
    let spawn = function_body(&source, "pub(crate) fn spawn_locald(");
    assert!(spawn.contains("command.process_group(0)"), "{spawn}");
}

/// Check for Updates… is always in the Lemma menu; an available update adds
/// one row after it, named for the version, and both open the update panel.
#[test]
fn the_lemma_menu_offers_an_update_check_and_names_a_waiting_update() {
    assert_eq!(
        lemma_menu_update_items(None),
        vec![("check-updates", "Check for Updates\u{2026}".to_owned())]
    );
    assert_eq!(
        lemma_menu_update_items(Some("0.9.1")),
        vec![
            ("check-updates", "Check for Updates\u{2026}".to_owned()),
            (
                "install-update",
                "Lemma 0.9.1 is available \u{2014} Install\u{2026}".to_owned()
            ),
        ]
    );
    let menus = include_str!("../menus.rs").replace("\r\n", "\n");
    assert!(menus.contains(
        "\"check-updates\" | \"install-update\" => {\n            let _ = show_control_center_page(&app, Some(\"updates\"));"
    ));
    // And the page knows what "updates" means in both modes.
    assert_eq!(control_center_page(Some("updates")).unwrap(), "updates");
    assert!(CONTROL.contains("\"updates\""));
}

#[test]
fn the_launch_update_check_runs_only_where_an_update_could_be_installed() {
    assert!(launch_update_check_wanted(true, false));
    assert!(
        !launch_update_check_wanted(false, false),
        "a dev or unsigned build"
    );
    assert!(
        !launch_update_check_wanted(true, true),
        "Recovery starts nothing"
    );
    let app = include_str!("../app.rs").replace("\r\n", "\n");
    assert!(app.contains("schedule_launch_update_check(&handle, recovery_launch);"));
}
