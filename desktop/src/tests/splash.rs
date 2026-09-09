use super::*;

#[test]
fn the_splash_draws_something_for_every_state() {
    // `renderState` is the only thing that paints the splash and the only
    // thing that schedules the move to the workspace. It used to return
    // early for a state with no phase, and again for an undecided
    // connection mode -- so those states left the bare static logo on
    // screen indefinitely, which is what a stuck first run looked like.
    let splash = include_str!("../../ui/index.html").replace("\r\n", "\n");
    let body = {
        let start = splash
            .find("function renderState(s) {")
            .expect("renderState exists");
        let end = splash[start..]
            .find("\n  function ")
            .map_or(splash.len(), |offset| start + offset);
        &splash[start..end]
    };
    assert!(
        !body.contains("if (!s || !s.phaseKey) return;"),
        "a state without a phase must still render"
    );
    assert!(
        !body.contains(r#"if (s.mode === "undecided") return;"#),
        "an undecided mode must show the chooser, not an empty screen"
    );
    assert!(
        body.contains("showChooser();"),
        "the undecided branch must offer the connection choice"
    );
}

/// The first screen a user sees is the product's colour, in both themes.
///
/// `control.css` rebound its accent to violet and the splash was left
/// behind, so the very first impression was a gold-and-cream screen and the
/// moment the workspace opened the product was violet. The splash also had
/// no dark palette at all, so on a dark-mode Mac -- most of them -- every
/// launch, every error and every shutdown flashed a full screen of cream.
#[test]
fn the_splash_is_the_products_colour_and_follows_the_system_theme() {
    let splash = include_str!("../../ui/index.html").replace("\r\n", "\n");

    for gold in [
        "#c0801f",
        "#8a5c16",
        "0xd89b3d",
        "216, 155, 61",
        "192, 128, 31",
    ] {
        assert!(
            !splash.contains(gold),
            "{gold} is the old accent; the product is violet",
        );
    }
    assert!(splash.contains("@media (prefers-color-scheme: dark)"));
    assert!(
        splash.contains("color-scheme: light dark"),
        "form controls and scrollbars follow the theme too",
    );
    // The classic half-themed bug: a colour whose only definition sits
    // inside the dark block is absent in light mode. Every token the dark
    // block redefines must also exist on the bare `:root`.
    let dark_block = splash
        .split("@media (prefers-color-scheme: dark)")
        .nth(1)
        .expect("the dark block exists");
    let dark_block = &dark_block[..dark_block.find("\n  }\n").unwrap_or(dark_block.len())];
    let root_block = splash
        .split(":root {")
        .nth(1)
        .expect("the light block exists");
    for line in dark_block.lines() {
        let Some(name) = line.trim().strip_prefix("--") else {
            continue;
        };
        let Some(name) = name.split(':').next() else {
            continue;
        };
        assert!(
            root_block.contains(&format!("--{name}:")),
            "--{name} is defined only in dark mode, so light mode has no value for it",
        );
    }
}

#[test]
fn a_refused_stop_leaves_no_splash_behind() {
    // The splash used to go up before the stop was sent, so a refusal left
    // a "stopping Lemma" screen in front of a stack nobody had asked to
    // stop, with no way back.
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let body = function_body(&source, "fn stop_impl(");
    let sent = body.find("send_local_operation").expect("stop_impl sends");
    let splash = body
        .find("show_splash_with_intent")
        .expect("stop_impl shows a splash");
    assert!(
        sent < splash,
        "the splash must follow the accepted operation, not precede it"
    );
}

#[test]
fn local_settings_does_not_inherit_the_splash_commands() {
    // Local settings is a child webview of the main window, and capability
    // matching is (window OR webview). A window-scoped splash capability
    // would therefore hand its commands to Local settings as well.
    for name in ["main", "control", "workspace", "confirmation"] {
        assert!(
            capability(name).get("windows").is_none(),
            "{name} must scope by webview, not window",
        );
    }
    assert_eq!(capability("main")["webviews"][0], "main");
    assert_eq!(capability("control")["webviews"][0], "control");

    let splash_only = [
        "allow-prepare-runtime",
        "allow-choose-connection-mode",
        "allow-login",
    ];
    for permission in splash_only {
        assert!(granted("main").contains(&permission.to_string()));
        assert!(!granted("control").contains(&permission.to_string()));
    }
    assert!(granted("control").contains(&"allow-apply-operator-config".to_string()));
    assert!(!granted("main").contains(&"allow-apply-operator-config".to_string()));
}

#[test]
fn the_window_layer_is_painted_in_both_appearances() {
    // Whatever the webview is not painting, this is. Neither may be the
    // macOS default of nothing, which composites black.
    assert_eq!(canvas_color(tauri::Theme::Dark), CANVAS_DARK);
    assert_eq!(canvas_color(tauri::Theme::Light), CANVAS_LIGHT);
}

/// Closing the workspace must not strand a pod app window.
///
/// Dropping to `Accessory` removes the Dock tile for the whole application,
/// so doing it while an app window is still on screen leaves that window
/// visible, un-⌘-tabbable, and owned by an app the Dock says is not running.
#[test]
fn the_dock_follows_what_is_actually_on_screen() {
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let start = source
        .find("fn settle_dock_presence(")
        .expect("the dock helper exists");
    let end = source[start..]
        .find("\nfn ")
        .map_or(source.len(), |offset| start + offset);
    let helper = &source[start..end];
    assert!(
        helper.contains("windows()") && helper.contains("is_visible"),
        "dock presence must be decided by what is visible, not by the \
         workspace window alone",
    );
    assert!(
        helper.contains("ActivationPolicy::Regular"),
        "something on screen has to put the Dock icon back",
    );
}

#[test]
fn ready_auto_navigation_only_treats_the_native_installer_as_splash() {
    assert!(native_splash_url(
        &tauri::Url::parse(&native_assets::url("index.html", None)).unwrap()
    ));
    assert!(native_splash_url(
        &tauri::Url::parse(&native_assets::url("", None)).unwrap()
    ));
    assert!(!native_splash_url(
        &tauri::Url::parse("http://app.lemma.localhost:3711/").unwrap()
    ));
    assert!(!native_splash_url(
        &tauri::Url::parse(&native_assets::url("control.html", None)).unwrap()
    ));
}

#[test]
fn the_splash_survives_the_navigation_gate_in_local_mode() {
    // Local mode denies local http destinations that are not the workspace,
    // which is what kept a dev build's splash off the screen: it is served
    // over loopback http, so it looked exactly like the thing that rule
    // exists to block.
    let splash = tauri::Url::parse(&native_asset_url("index.html")).unwrap();
    assert_eq!(
        navigation_disposition(
            &splash,
            "local",
            "http://app.lemma.localhost:52501",
            "http://app.lemma.localhost:52502",
        ),
        NavigationDisposition::Allow,
        "the splash must be allowed to load: {splash}"
    );
}
