use super::*;

#[test]
fn a_menu_verb_runs_once() {
    // `app.on_menu_event` receives events from every menu the app owns, the
    // tray's included. The tray builder registered a second handler on top,
    // so every tray verb ran twice: two confirmation dialogs stacked on each
    // other, two stops, two restarts.
    // The needle is assembled at compile time so it never appears whole in
    // this file -- a source-scanning test that spells out what it is looking
    // for finds itself, which is how the first two versions of this failed
    // on the fix they were written to protect.
    let needle = concat!("on_menu_event", "(|app, event|");
    let source = shell_source();
    assert_eq!(
        source.matches(needle).count(),
        1,
        "exactly one menu event handler, or every verb fires once per handler"
    );
}

#[test]
fn the_tray_lock_is_not_held_across_main_thread_round_trips() {
    // Every `set_*` on a menu item blocks until the main thread is free, and
    // this runs on the locald reader thread -- so holding the lock across
    // them stopped daemon events being read whenever the main thread was
    // busy. Progress froze and `ready` was never handled.
    let source = shell_source();
    let body = function_body(&source, "fn refresh_agent_host_tray(");
    let guard_end = body
        .find("guard.clone()")
        .expect("the handles are cloned out of the guard");
    let first_set = body.find(".set_text(").expect("the tray text is set");
    assert!(
        guard_end < first_set,
        "clone the menu handles and drop the guard before any set_* call"
    );
}

#[test]
fn the_tray_reports_reachability_rather_than_liveness() {
    // Each of these is a live process that cannot take work, and the old
    // process-only status called them all "running".
    assert_eq!(
        agent_host_tray_label(true, true, false, false, false, false),
        "Agent Host: not paired",
    );
    assert_eq!(
        agent_host_tray_label(true, true, true, false, false, false),
        "Agent Host: reconnecting…",
    );
    assert_eq!(
        agent_host_tray_label(true, true, true, true, false, false),
        "Agent Host: connected",
    );
    // Paired and not running is a stage, not a setting: the off switch is
    // gone, so this state is only ever on its way to "connected".
    assert_eq!(
        agent_host_tray_label(true, false, true, false, false, false),
        "Agent Host: starting…"
    );
    // A build without the sidecar has no host to report on at all.
    assert_eq!(
        agent_host_tray_label(false, false, false, false, false, false),
        "Agent Host: not installed",
    );
    // The state this distinction was added for: a workspace that answered
    // yesterday and is not there today. "Reconnecting" for a week is a
    // promise the retry loop cannot keep.
    assert_eq!(
        agent_host_tray_label(true, true, true, false, true, false),
        "Agent Host: workspace unreachable",
    );
    // Connected wins over a stale error on another target: one workspace
    // failing does not stop this computer taking work from the other.
    assert_eq!(
        agent_host_tray_label(true, true, true, true, true, false),
        "Agent Host: connected",
    );
}

#[test]
fn choosing_a_connection_mode_re_enables_the_menus_it_gates() {
    // Both menus gate their local-only verbs on `local`, and both are built
    // during setup — which on a first launch is before anyone has chosen.
    // Every gated item was created disabled and never revisited, so picking
    // Local left "Local settings…" greyed out in the tray and Cmd-, dead in
    // the app menu until Lemma was restarted.
    //
    // Asserted on the source because the alternative needs a running
    // AppHandle, and the thing worth pinning is that the one function every
    // mode change goes through is what rebuilds them.
    let source = include_str!("../connection.rs").replace("\r\n", "\n");
    let set_mode = function_body(&source, "fn set_mode(app: &AppHandle, mode: &str)");
    assert!(
        set_mode.contains("refresh_menus_for_connection_mode(app)"),
        "set_mode must rebuild the menus, or the choice does not take effect \
         until the next launch"
    );
}

#[test]
fn the_menu_bar_speaks_the_products_language() {
    // There was no app menu at all before this: the shipped build used
    // Tauri's default, so there was no Cmd-, and every Lemma verb was in
    // the tray. The tray in turn read as a supervisor console.
    // Scoped to the two menu builders rather than the whole file, because
    // a test that scans its own source matches the very strings it is
    // asserting are gone.
    let source = include_str!("../menus.rs").replace("\r\n", "\n");
    let menus = {
        let start = source
            .find("fn build_app_menu")
            .expect("the app menu builder exists");
        // The whole module: both builders live here now, and it holds
        // nothing else.
        &source[start..]
    };

    assert!(menus
        .split_whitespace()
        .collect::<String>()
        .contains("\"Desktopsettings…\",true,Some(\"CmdOrCtrl+,\")"));
    assert!(menus.contains("\"Connection…\""));

    // Quit is one command, and it is the one that stops the local server.
    // There used to be two ways to leave — Quit, which left the VM and the
    // whole stack running with no owner on screen, and a "Stop the local
    // server and quit" buried in Troubleshoot that did what people mean by
    // quitting. Closing the window already covers "go away and keep
    // serving", and keeps the tray as a way back, so the middle state was
    // strictly worse than both neighbours.
    assert!(
        !menus.contains("quit-and-stop") && !menus.contains("Stop the local server and quit"),
        "there is exactly one Quit, and it stops the local server",
    );
    assert!(menus.contains("\"Stop the local server\""));

    // ⌘Q has to reach `request_quit`, which means owning the item. AppKit's
    // predefined quit is `terminate:` and cannot be intercepted, so it would
    // silently skip the prompt and take the stack down — or leave it up —
    // without asking. Matched as a call, because this test reads its own
    // source and the comment above the item names the thing it rules out.
    assert!(
        menus.contains("\"quit\", \"Quit Lemma\", true, Some(\"CmdOrCtrl+Q\")"),
        "Quit is an app-owned item bound to CmdOrCtrl+Q",
    );
    assert!(
        !menus.contains("PredefinedMenuItem::quit("),
        "a predefined quit cannot be asked about first",
    );

    // The operator vocabulary this replaced must not come back.
    for retired in [
        "Stop Services and Infra",
        "Switch Connection Mode",
        "Start Services",
    ] {
        assert!(
            !menus.contains(retired),
            "{retired:?} is supervisor vocabulary, not a product menu item"
        );
    }
}

/// Every Agent Host command checks who is calling it.
///
/// `workspace.json` grants these to the workspace origin, and the origin is
/// whatever the main window is currently showing -- so the check is what
/// stands between a page and this computer's Agent Host. `agent_host_status`
/// was the one command without it: any page in the main window could start
/// locald and read the pairing state back.
#[test]
fn every_agent_host_command_checks_its_caller() {
    let source = shell_source();
    let mut unchecked = Vec::new();
    for (index, _) in source.match_indices("#[tauri::command") {
        let rest = &source[index..];
        let Some(at) = rest.find("fn agent_host_") else {
            continue;
        };
        // The attribute belongs to this function only if no other function
        // begins between them.
        let between = &rest[..at];
        if [
            "\nfn ",
            "\nasync fn ",
            "\npub(crate) fn ",
            "\npub(crate) async fn ",
        ]
        .iter()
        .any(|marker| between.contains(marker))
        {
            continue;
        }
        let name: String = rest[at + "fn ".len()..]
            .chars()
            .take_while(|c| c.is_alphanumeric() || *c == '_')
            .collect();
        let body = function_body(&source, &format!("fn {name}("));
        // `require_control_window` is the stricter of the two: it admits only
        // Local settings, where `require_agent_host_caller` also admits the
        // signed-in workspace. `agent_host_action` is granted by
        // `control.json` alone and takes that one.
        if !body.contains("require_agent_host_caller(") && !body.contains("require_control_window(")
        {
            unchecked.push(name);
        }
    }
    assert!(
        unchecked.is_empty(),
        "these Agent Host commands do not check their caller: {unchecked:?}",
    );
    // Not vacuous: there are seven of them.
    let checked = source
        .matches("require_agent_host_caller(&window, &app)?")
        .count();
    assert!(checked >= 7, "only {checked} commands check their caller");
}
