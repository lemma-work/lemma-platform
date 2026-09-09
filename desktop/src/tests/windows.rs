use super::*;

/// Uninstalling asked to delete Lemma's data and deleted nothing.
///
/// Tauri's uninstaller offers a "delete application data" checkbox, and
/// what it removes is `%APPDATA%\<identifier>` and
/// `%LOCALAPPDATA%\<identifier>` -- work.lemma.desktop. Lemma's data is in
/// `%LOCALAPPDATA%\Lemma`, and most of it is not files at all: it is a
/// registered WSL distribution whose ext4.vhdx holds every workspace,
/// database and image. So the box removed nothing anybody had, and an
/// uninstall left several gigabytes and a registered distribution that
/// only `wsl --unregister` could clear.
///
/// The hook is what closes that, and these are the three things about it
/// that have to stay true. It cannot be executed from here -- NSIS runs
/// only on Windows, and only during a real uninstall -- so CI's Windows
/// job building the installer is what proves it parses.
#[test]
fn the_windows_uninstaller_removes_the_data_the_checkbox_promises() {
    // Normalised: CI's Windows runner checks the tree out with CRLF, and a
    // needle spanning a line break would find nothing there.
    let config = include_str!("../../tauri.windows.conf.json").replace("\r\n", "\n");
    let hooks = include_str!("../../installer/hooks.nsh").replace("\r\n", "\n");

    assert!(
        config.contains(r#""installerHooks": "installer/hooks.nsh""#),
        "the uninstaller's data checkbox does nothing without this hook"
    );
    // Pinned rather than left to Tauri's default, which decides whether
    // the app lands in Program Files and needs Administrator.
    assert!(
        config.contains(r#""installMode": "currentUser""#),
        "the install mode has to be a decision here, not a default \
         elsewhere that can change under us"
    );

    assert!(
        hooks.contains("!macro NSIS_HOOK_PREUNINSTALL"),
        "PRE, not POST: by POSTUNINSTALL lemma-locald.exe has already been \
         deleted, and it is the thing that knows how to unregister the guest"
    );
    assert!(
        !hooks.contains("NSIS_HOOK_POSTUNINSTALL"),
        "nothing can run from $INSTDIR after the files are gone"
    );
    assert!(
        hooks.contains("$DeleteAppDataCheckboxState = 1") && hooks.contains("$UpdateMode <> 1"),
        "erasing local data must happen only when it was asked for, and \
         never during an update"
    );
    let purge = hooks
        .find("reset --confirm=erase-local-lemma")
        .expect("the hook runs locald's own reset rather than reimplementing it");
    let stop = hooks
        .find("taskkill /F /T /IM lemma-locald.exe")
        .expect("reset refuses while the daemon is answering, so it stops first");
    assert!(
        stop < purge,
        "the daemon has to be stopped before the reset, or the reset refuses"
    );
}

#[test]
fn a_replaced_window_is_measured_before_it_is_destroyed() {
    // Read order, not a nicety: `outer_position` and `inner_size` need a
    // window, and the whole point is that there is about to not be one. The
    // rebuilt window used to come back at the OS default placement in the
    // default size, which is a swap the user sees even if nothing flashes.
    //
    // Asserted on the source for the same reason as the wait below: reaching
    // the real path needs a running event loop.
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let body = {
        let start = source
            .find("fn rebuild_main_window_for_mode(app: &AppHandle, mode: &str)")
            .expect("rebuild_main_window_for_mode exists");
        let end = source[start..]
            .find("\nfn ")
            .map_or(source.len(), |offset| start + offset);
        &source[start..end]
    };

    let measured = body
        .find("placement_of(&existing)")
        .expect("it reads where the window was");
    let destroy = body.find(".destroy()").expect("it destroys the old window");

    assert!(
        measured < destroy,
        "the window has to be measured while it still exists"
    );
    assert!(
        body.contains("build_main_window_at(app, mode, initial.clone(), true, true, placement)"),
        "the replacement is told it is one, and where to sit"
    );
}

#[test]
fn a_window_is_only_restored_where_a_hand_can_reach_it() {
    let screen = |x, y, w, h| {
        (
            tauri::PhysicalPosition::new(x, y),
            tauri::PhysicalSize::new(w, h),
        )
    };
    let at = |x, y| WindowPlacement {
        position: tauri::PhysicalPosition::new(x, y),
        size: tauri::PhysicalSize::new(1280, 860),
    };
    let laptop = [screen(0, 0, 1728, 1117)];
    // The same desk, with the external display to the left -- which is
    // where negative coordinates come from and why this cannot just clamp
    // to zero.
    let two_displays = [screen(0, 0, 1728, 1117), screen(-3440, -200, 3440, 1440)];

    assert!(placement_is_reachable(&at(100, 100), &laptop));
    assert!(placement_is_reachable(&at(-3000, 0), &two_displays));
    assert!(
        !placement_is_reachable(&at(-3000, 0), &laptop),
        "the second display is gone; this window would be invisible"
    );

    // A window may legitimately hang off an edge. What must stay on screen
    // is enough of the title bar to grab -- and "some overlap" is not that.
    assert!(
        placement_is_reachable(&at(1600, 20), &laptop),
        "mostly off the right edge, but 128px of title bar is draggable"
    );
    assert!(
        !placement_is_reachable(&at(1700, 20), &laptop),
        "28px of chrome poking over the edge is not something a hand catches"
    );
    assert!(
        !placement_is_reachable(&at(200, 1100), &laptop),
        "the title bar is below the display, so there is nothing to drag"
    );
    assert!(
        !placement_is_reachable(&at(200, -90), &laptop),
        "the title bar is above the display"
    );

    // Asking the OS can fail, and a machine mid-display-change reports no
    // monitors at all. Neither is evidence the saved value is wrong, and
    // refusing to restore there would look like the bug this fixes.
    assert!(placement_is_reachable(&at(100, 100), &[]));
}

#[test]
fn a_restored_window_comes_back_the_size_it_was_left() {
    // The numbers are a real record from a 3024x1964 Retina display: the
    // window filled the screen below the menu bar. Handed to the builder as
    // written they mean 3024x1898 *logical* -- twice the screen -- so macOS
    // clamped the size and put the window 66 points too low, which is what
    // "it doesn't open full and the top is cut off" actually was.
    let saved = WindowPlacement {
        position: tauri::PhysicalPosition::new(0, 66),
        size: tauri::PhysicalSize::new(3024, 1898),
    };

    let (position, size) = placement_in_logical(&saved, 2.0).expect("restorable");
    assert_eq!((position.x, position.y), (0.0, 33.0));
    assert_eq!((size.width, size.height), (1512.0, 949.0));

    // A 1:1 display is the case that always worked, and must keep working.
    let (position, size) = placement_in_logical(&saved, 1.0).expect("restorable");
    assert_eq!((position.x, position.y), (0.0, 66.0));
    assert_eq!((size.width, size.height), (3024.0, 1898.0));
}

#[test]
fn a_window_smaller_than_the_app_allows_is_not_restored() {
    // 1200x800 physical is a legitimate record on a 1:1 screen and half the
    // app's minimum on a 2x one. The floor is a logical size, so it can
    // only be applied after the conversion -- applying it to the physical
    // numbers is how a window half the allowed size gets restored and then
    // clamped by the OS into a shape nobody chose.
    let saved = WindowPlacement {
        position: tauri::PhysicalPosition::new(0, 0),
        size: tauri::PhysicalSize::new(1200, 800),
    };
    assert!(placement_in_logical(&saved, 1.0).is_some());
    assert!(
        placement_in_logical(&saved, 2.0).is_none(),
        "600x400 logical is below the {}x{} minimum",
        MIN_RESTORED.0,
        MIN_RESTORED.1
    );
    // A monitor that reports nonsense must not produce an infinite window.
    assert!(placement_in_logical(&saved, 0.0).is_none());
    assert!(placement_in_logical(&saved, f64::NAN).is_none());
}

/// A rebuild's own placement always wins over the remembered one.
///
/// The two are different questions: a rebuild is "put it back exactly where
/// the user is looking", and a cold start is "open it where they left it
/// last time". Reading the remembered value first would move a window
/// during a server switch, which is the bug `WindowPlacement` was
/// introduced to fix.
#[test]
fn a_rebuild_keeps_the_window_where_it_is_rather_than_where_it_once_was() {
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    assert!(
        source.contains("let placement = placement.or_else(|| remembered_placement(handle));"),
        "the caller's placement has to take precedence",
    );
}

#[test]
fn the_window_swap_waits_between_destroying_and_rebuilding() {
    // The bug this pins: `destroy` returns as soon as the request is
    // posted, so building immediately afterwards failed with `a webview
    // with label `main` already exists` -- and because the fallback ran in
    // the same millisecond it failed identically, leaving the app running
    // with no window at all. Both build attempts have to be preceded by a
    // wait.
    //
    // Asserted on the source because reaching the real path needs a running
    // event loop; `wait_until_label_released` itself is tested above.
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let body = {
        let start = source
            .find("fn rebuild_main_window_for_mode(app: &AppHandle, mode: &str)")
            .expect("rebuild_main_window_for_mode exists");
        let end = source[start..]
            .find("\nfn ")
            .map_or(source.len(), |offset| start + offset);
        &source[start..end]
    };

    let destroy = body.find(".destroy()").expect("it destroys the old window");
    let first_build = body
        .find("build_main_window_at(app, mode, initial.clone(), true, true, placement)")
        .expect("it rebuilds partitioned");
    let fallback = body
        .find("build_main_window_at(app, mode, initial, false, true, placement)")
        .expect("it falls back to the shared store");
    let waits: Vec<usize> = body
        .match_indices("wait_until_label_released")
        .map(|(index, _)| index)
        .collect();

    assert_eq!(waits.len(), 2, "every rebuild attempt waits for the label");
    assert!(
        destroy < waits[0] && waits[0] < first_build,
        "the partitioned rebuild must wait after destroy"
    );
    assert!(
        waits[1] < fallback,
        "the shared-store fallback must wait too, or it repeats the failure \
         it is supposed to recover from"
    );
}

#[test]
fn a_ready_event_rescues_a_window_left_on_a_stale_workspace() {
    // Three cases, and the middle one is the bug: the window is showing a
    // workspace, just not this one. The old test was "is it the splash?",
    // which answered no and left it there.
    let splash = tauri::Url::parse(&native_asset_url("index.html")).unwrap();
    let current = tauri::Url::parse("http://app.lemma.localhost:49180/pod/7").unwrap();
    let stale = tauri::Url::parse("http://app.lemma.localhost:57919/pod/7").unwrap();
    let workspace = "http://app.lemma.localhost:49180";

    let needs = |url: &tauri::Url| native_splash_url(url) || !same_origin(url, workspace);

    assert!(needs(&splash), "the ordinary first start");
    assert!(needs(&stale), "a resume whose stack was replaced under it");
    assert!(
        !needs(&current),
        "a stable workspace must never be navigated for a transient event",
    );
}

#[test]
fn privileged_configuration_commands_are_control_window_only() {
    assert!(is_control_window_label("control"));
    assert!(!is_control_window_label("main"));
    assert!(!is_control_window_label("sales.apps.lemma.localhost"));
}

#[test]
fn explicit_new_windows_keep_the_browser_policy() {
    let app_base = "https://lemma.work";
    let first_party = tauri::Url::parse("https://lemma.work/docs").unwrap();
    let external = tauri::Url::parse("https://widgets.example.com/report").unwrap();
    let blank = tauri::Url::parse("about:blank").unwrap();

    assert_eq!(
        new_window_disposition(&first_party, "hosted", app_base, ""),
        NewWindowDisposition::NavigateInApp
    );
    assert_eq!(
        new_window_disposition(&external, "hosted", app_base, ""),
        NewWindowDisposition::OpenExternal
    );
    assert_eq!(
        new_window_disposition(&blank, "hosted", app_base, ""),
        NewWindowDisposition::Deny
    );
}

/// A pod app window is a window, not a viewer: it can download.
///
/// Tauri cancels a download outright when a webview registers no policy,
/// silently -- the failure this repo already hit once, where every Download
/// button on macOS did nothing and said nothing. An app that exports a CSV
/// is an ordinary app, and it opens in this window now.
#[test]
fn the_pod_app_window_can_download_what_an_app_offers() {
    let source = include_str!("../main.rs").replace("\r\n", "\n");
    let start = source
        .find("fn open_pod_app_window(")
        .expect("the app window builder exists");
    let end = source[start..]
        .find("\nfn ")
        .map_or(source.len(), |offset| start + offset);
    let builder = &source[start..end];
    assert!(
        builder.contains(".on_download("),
        "the app window registers no download policy, so downloads from an \
         app are cancelled with no error",
    );
    assert!(
        builder.contains("download_disposition"),
        "the app window must be held to the same download policy as the \
         workspace, not a looser one of its own",
    );
}

/// The app window is deliberately outside every capability file.
///
/// Capabilities are scoped by webview label, so a label with no entry gets
/// no Tauri command surface at all. That is the whole reason a pod app can
/// have a window: it runs user-authored code, and an entry here would hand
/// it the IPC bridge.
#[test]
fn the_pod_app_window_is_granted_no_commands() {
    for (name, source) in [
        ("main", include_str!("../../capabilities/main.json")),
        ("control", include_str!("../../capabilities/control.json")),
        (
            "workspace",
            include_str!("../../capabilities/workspace.json"),
        ),
    ] {
        assert!(
            !source.contains(POD_APP_WINDOW),
            "{name}.json names the pod-app window, which would give \
             user-authored app code a command surface"
        );
    }
}

/// A Windows user is never told about hardware they do not have.
///
/// Both bundled pages ship in the Windows build. Every sentence about the
/// machine said "this Mac" -- including the recovery panel that names what
/// is about to be deleted, which is the worst possible place to describe
/// somebody else's computer.
///
/// The three static splash strings used to be rewritten one element id at a
/// time, so the lines written from JS -- the boot subtitle, the ready
/// subtitle, the question the local-install screen asks -- were simply
/// missed. What is asserted here is the mechanism, not a list of strings:
/// a whole-document pass plus the two places text is produced after it.
/// A new "this Mac" anywhere is then covered without anyone remembering to
/// extend anything.
#[test]
fn every_page_that_ships_on_windows_renames_the_machine() {
    let splash = include_str!("../../ui/index.html").replace("\r\n", "\n");
    let control = include_str!("../../ui/control.js").replace("\r\n", "\n");

    for (page, source) in [("index.html", &splash), ("control.js", &control)] {
        assert!(
            source.contains(r#"replace(/\bthis Mac\b/g, "this PC")"#),
            "{page} has no device rewrite, so its copy is Mac-only",
        );
        assert!(
            source.contains("NodeFilter.SHOW_TEXT"),
            "{page} must rewrite the whole document, not a list of ids",
        );
        // Both pages carry inline or loaded script; rewriting a SCRIPT text
        // node changes nothing anyone reads and leaves a DOM that no longer
        // matches the file on disk.
        assert!(
            source.contains("SCRIPT|STYLE"),
            "{page} rewrites script text as well as copy",
        );
    }

    // The splash produces text after the document pass has run. Both
    // producers have to go through the rewrite or the pass covers only the
    // half of the copy that happens to be static.
    assert!(
        splash.contains("VOICE[key] = VOICE[key].map(forThisDevice)"),
        "the phase table is written after the walk and needs its own pass",
    );
    assert!(
        splash.contains("const text = forThisDevice(rawText);"),
        "say() is the only writer of the serif line and must rewrite too",
    );
    // The settings window routes every error through one formatter.
    assert!(
        control.contains("return forThisDevice(match ? match[1]"),
        "friendlyError is where the recovery copy is written",
    );
    // And the one sentence that is about the operating system rather than
    // the box it runs on is named per platform, not rewritten.
    assert!(control.contains(r#"IS_WINDOWS ? "Windows" : "macOS""#));
}

/// Uninstalling asked to delete Lemma's data and deleted nothing.
///
/// Tauri's uninstaller offers a "delete application data" checkbox, and
/// what it removes is `%APPDATA%\<identifier>` and
/// `%LOCALAPPDATA%\<identifier>` -- work.lemma.desktop. Lemma's data is in
/// `%LOCALAPPDATA%\Lemma`, and most of it is not files at all: it is a
/// registered WSL distribution whose ext4.vhdx holds every workspace,
/// database and image. So the box removed nothing anybody had, and an
/// uninstall left several gigabytes and a registered distribution that
/// only `wsl --unregister` could clear.
///
/// The hook is what closes that, and these are the three things about it
/// that have to stay true. It cannot be executed from here -- NSIS runs
/// only on Windows, and only during a real uninstall -- so CI's Windows
/// job building the installer is what proves it parses.
/// Clicking the Dock icon with no window open did nothing at all.
///
/// On macOS closing the last window leaves the app running, so this is the
/// one gesture whose whole purpose is bringing it back -- and the arm that
/// handles it only ever showed a window that already existed. This is the
/// part of the fix with cases in it; the arm itself needs a Tauri runtime.
#[cfg(target_os = "macos")]
#[test]
fn reopening_with_every_window_closed_goes_somewhere() {
    assert_eq!(
        reopen_target("local", true, false, "http://app.127.0.0.1.sslip.io:1/"),
        ReopenTarget::Workspace("http://app.127.0.0.1.sslip.io:1/".into()),
        "a running local stack goes straight back to the workspace"
    );

    // Hosted has no local stack to be ready for.
    assert_eq!(
        reopen_target("hosted", false, false, ""),
        ReopenTarget::Hosted
    );

    for (label, ready, error, url) in [
        ("still starting", false, false, ""),
        ("failed", true, true, "http://app.127.0.0.1.sslip.io:1/"),
        ("ready but with no url yet", true, false, ""),
        (
            "undecided mode",
            true,
            false,
            "http://app.127.0.0.1.sslip.io:1/",
        ),
    ] {
        let mode = if label == "undecided mode" {
            "undecided"
        } else {
            "local"
        };
        assert_eq!(
            reopen_target(mode, ready, error, url),
            ReopenTarget::Splash,
            "{label}: the splash is where the state and the actions are"
        );
    }
}
