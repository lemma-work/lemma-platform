use super::*;

#[test]
fn a_placement_this_build_would_not_have_written_is_ignored() {
    let saved = |value: serde_json::Value| saved_placement(&json!({ "window": value }));

    assert_eq!(
        saved(json!({"x": 12, "y": 34, "width": 1280, "height": 860})),
        Some(WindowPlacement {
            position: tauri::PhysicalPosition::new(12, 34),
            size: tauri::PhysicalSize::new(1280, 860),
        })
    );
    // Below `min_inner_size`: written by a build with different minimums,
    // or captured mid-animation. The OS would clamp it and the window would
    // come back a shape nobody chose.
    assert_eq!(
        saved(json!({"x": 0, "y": 0, "width": 400, "height": 300})),
        None
    );
    // A truncated or hand-edited file must not stop the app opening.
    assert_eq!(saved(json!({"x": 0, "y": 0, "width": 1280})), None);
    assert_eq!(
        saved(json!({"x": "left", "y": 0, "width": 1280, "height": 860})),
        None
    );
    assert_eq!(saved(json!(null)), None);
    assert_eq!(saved_placement(&json!({})), None, "a first-ever launch");
    // Negative sizes and values past a u32 are refused rather than wrapped.
    assert_eq!(
        saved(json!({"x": 0, "y": 0, "width": -1280, "height": 860})),
        None
    );
}

/// Geometry is recorded as it changes, not only when the app is quit.
///
/// An app that is force-killed, replaced by an updater, or caught in a
/// machine restart never sees a close event -- and those are exactly the
/// launches where coming back at the wrong size is most irritating.
#[test]
fn window_geometry_survives_a_launch_that_was_never_a_clean_quit() {
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let start = source
        .find("fn build_main_window_at(")
        .expect("build_main_window_at exists");
    let body = &source[start..];
    let moved = body
        .find("tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_)")
        .expect("placement is recorded on move and resize");
    assert!(
        body[moved..moved + 200].contains("remember_placement(&window)"),
        "both events have to write the placement"
    );
}

#[test]
fn a_replacement_window_is_never_left_invisible() {
    // Hidden-until-painted is only safe because something shows it anyway.
    // A window that never paints and never appears is an app with no
    // interface, which is the failure the label wait already guards.
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let start = source
        .find("fn build_main_window_at(")
        .expect("build_main_window_at exists");
    let body = &source[start..];

    assert!(
        body.contains("REPLACEMENT_REVEAL_TIMEOUT"),
        "the reveal has a deadline"
    );
    let hidden = body
        .find(".visible(false)")
        .expect("a replacement starts hidden");
    let backstop = body
        .find("REPLACEMENT_REVEAL_TIMEOUT")
        .expect("the deadline is used");
    assert!(
        hidden < backstop,
        "hidden first, then the backstop that shows it"
    );
}

#[test]
fn the_splash_is_reachable_in_the_build_we_are_running() {
    // `tauri://localhost` does not exist under `cargo tauri dev` — the CLI
    // serves frontendDist over loopback instead. Navigating there anyway
    // left the window white on "index.html not found" until the workspace
    // URL replaced it a minute later, so every startup stage the splash
    // exists to show went unseen.
    let splash = tauri::Url::parse(&native_asset_url("index.html")).unwrap();
    assert!(
        native_splash_url(&splash),
        "the URL show_splash navigates to must be recognised as the splash: {splash}"
    );
    assert!(
        trusted_native_asset_url(&splash),
        "and must be a trusted native asset origin: {splash}"
    );
    if cfg!(debug_assertions) {
        assert_eq!(splash.port(), Some(DEV_ASSET_PORT));
    } else {
        assert_eq!(
            splash.scheme(),
            if cfg!(windows) { "http" } else { "tauri" }
        );
    }
}

#[test]
fn desktop_config_replacement_never_exposes_a_partial_file() {
    let root = tempfile::tempdir().unwrap();
    let destination = root.path().join("desktop-config.json");
    let source = root.path().join("desktop-config.json.next");
    std::fs::write(&destination, br#"{"revision":1}"#).unwrap();
    std::fs::write(&source, br#"{"revision":2}"#).unwrap();

    config_store::replace(&source, &destination).unwrap();

    assert_eq!(
        std::fs::read_to_string(&destination).unwrap(),
        r#"{"revision":2}"#
    );
    assert!(!source.exists());
}
