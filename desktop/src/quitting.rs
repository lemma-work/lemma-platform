use super::*;

/// What quitting takes away, in the user's terms, or nothing.
///
/// Quit stops the local server, and the local server is the only thing that
/// runs this installation's schedules, answers for the agents on this computer,
/// and serves any link the user has shared. None of that is on screen, so a
/// silent quit is a silent loss. An empty list means there is genuinely nothing
/// to lose and quitting needs no ceremony.
///
/// Everything here is read from state the shell already holds. The quit path is
/// a keystroke, and a stack too sick to answer a snapshot is exactly the state
/// someone quits from — so it must not depend on the daemon replying.
pub(crate) fn quit_impact(app: &AppHandle) -> Vec<String> {
    // The mode check used to wrap the whole function, so a hosted user was
    // never told anything and quit without a prompt at all. But locald is
    // brought up in hosted mode precisely so the Agent Host can run, and a
    // full quit stops it -- so somebody with a coding agent mid-run lost it
    // silently, while a local user got a careful three-line warning.
    //
    // Only the *stack* line is local-only. The Agent Host runs in both.
    let local = current_mode(app) == "local";
    let shell: State<Shell> = app.state();
    let stack_up = local && {
        let ui = shell.ui.lock().unwrap();
        ui.ready || ui.running || !ui.active_operation_id.is_empty()
    };
    let agent_host = shell.agent_host_status.lock().unwrap().clone();
    let sharing = if local {
        shell.sharing_mode.lock().unwrap().clone()
    } else {
        None
    };
    quit_impact_lines(stack_up, agent_host.as_ref(), sharing.as_deref())
}

pub(crate) fn quit_impact_lines(
    stack_up: bool,
    agent_host: Option<&Value>,
    sharing: Option<&str>,
) -> Vec<String> {
    let mut impact = Vec::new();
    if stack_up {
        impact.push("Schedules and background work stop running.".into());
    }
    if let Some(status) = agent_host {
        if status["running"].as_bool() == Some(true) {
            let paired = status["targets"]
                .as_array()
                .map(|targets| targets.len())
                .unwrap_or(0);
            impact.push(match paired {
                0 => "The agents on this computer stop answering.".into(),
                1 => "The agents on this computer stop answering (1 paired workspace).".into(),
                many => format!(
                    "The agents on this computer stop answering ({many} paired workspaces)."
                ),
            });
        }
    }
    match sharing {
        Some("local_network") => impact.push("Your local network link closes.".into()),
        Some("public") => impact.push("Your public link closes.".into()),
        _ => {}
    }
    impact
}

pub(crate) fn quit_prompt_body(impact: &[String]) -> String {
    let mut body = format!("Quitting stops Lemma's local server on {THIS_COMPUTER}.\n\n");
    for line in impact {
        body.push_str("•  ");
        body.push_str(line);
        body.push('\n');
    }
    // Both halves matter. The first is why this is safe to say yes to; the
    // second is the answer for someone who pressed ⌘Q meaning "get out of my
    // way", which closing the window already does without stopping anything.
    body.push_str(&format!(
        "\nPods, files, and data stay on {THIS_COMPUTER} and come back when you reopen \
             Lemma.\nTo leave Lemma running, close the window instead."
    ));
    body
}

/// Quit, having said what that costs.
///
/// One Quit. It used to mean "close the shell and leave the server running",
/// which was both a third state — closing the window already does exactly that,
/// and keeps the tray as a way back — and a quiet inversion: it stopped sharing
/// and the Agent Host, the cheap visible things, while leaving the VM, Postgres
/// and the backend running with no owner on screen at all.
pub(crate) fn request_quit(app: &AppHandle) {
    {
        // Repeated shortcuts must not silently interrupt a migration. A slow
        // shutdown offers its explicit fallback in the app instead.
        let shell: State<Shell> = app.state();
        if shell.quit_confirmed.load(Ordering::Acquire) {
            return;
        }
    }
    let impact = quit_impact(app);
    if impact.is_empty() {
        let handle = app.clone();
        std::thread::spawn(move || stop_then_quit(&handle));
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        match confirm_destructive_action_impl(
            handle.clone(),
            "Stop Lemma and quit?".into(),
            quit_prompt_body(&impact),
            "Stop and Quit".into(),
        ) {
            Ok(true) => stop_then_quit(&handle),
            Ok(false) => {}
            Err(error) => report_action_failure(&handle, "Quit Lemma", &error),
        }
    });
}

pub(crate) fn stop_then_quit(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    shell.quit_confirmed.store(true, Ordering::Release);
    // This worker must persist the live route before shutdown replaces it with
    // the splash. A detached write can be lost when the daemon exits quickly.
    if let Some(route) =
        read_resume_target().and_then(|target| current_workspace_route(app, &target))
    {
        write_resume_route(&route);
    }
    if let Some(control) = app.get_webview("control") {
        let _ = control.close();
    }
    shell.quit_after_stop.store(true, Ordering::Release);
    if shell.locald_writer.lock().unwrap().is_none() {
        match connect_locald() {
            Ok(connection) => install_locald_connection(app, connection),
            Err(_) => {
                finish_quit(app);
                return;
            }
        }
    }
    if let Err(error) = send_local_operation(
        app,
        json!({"cmd": "shutdown-daemon"}),
        operation_id("shell-quit"),
    ) {
        shell.quit_after_stop.store(false, Ordering::Release);
        shell.quit_confirmed.store(false, Ordering::Release);
        // Confirming "Stop and Quit" and then getting neither, silently, is the
        // worst version of this. Say why the quit did not happen; the dialog
        // also tells the user that trying again is the next move.
        report_action_failure(app, "Stop Lemma and quit", &error);
        return;
    }
    show_splash_with_intent(app, "quit");
    // Nothing else bounds this. `quit_after_stop` is consumed only by a `done`
    // event that says the stop succeeded, so any other outcome -- including no
    // outcome -- leaves the app running with the user's quit unanswered.
    let handle = app.clone();
    std::thread::spawn(move || {
        run_quit_watchdog(
            || std::thread::sleep(QUIT_STOP_BUDGET),
            || {
                handle
                    .state::<Shell>()
                    .quit_after_stop
                    .load(Ordering::Acquire)
            },
            || {
                append_install_log(&format!(
                    "quit: the stop did not finish within {}s; offering to quit anyway",
                    QUIT_STOP_BUDGET.as_secs()
                ));
                confirm_destructive_action_impl(
                    handle.clone(),
                    "Lemma is taking longer than usual to stop.".into(),
                    "Lemma is waiting for local work to stop safely. Keep waiting while \
                     a database migration or installation finishes. Quit Anyway may interrupt \
                     that work and require recovery when Lemma next starts."
                        .into(),
                    "Quit Anyway".into(),
                )
                .unwrap_or(false)
            },
            || {
                handle
                    .state::<Shell>()
                    .quit_after_stop
                    .store(true, Ordering::Release);
            },
            || finish_quit(&handle),
        );
    });
}

/// What to tell someone whose update failed after their stack was stopped.
///
/// The install runs with local services deliberately down, so a failure here
/// leaves the machine in a state the user did not ask for and cannot see the
/// cause of. Both halves matter: that the version they had is intact, and
/// whether it is running again. Saying only "could not install the update"
/// left them looking at a settings window over a dead workspace.
pub(crate) fn failed_install_message(install_error: &str, restart_error: Option<String>) -> String {
    match restart_error {
        None => format!(
            "could not install the update: {install_error}. Your previous version is \
             still installed and its services are starting again."
        ),
        Some(restart_error) => format!(
            "could not install the update: {install_error}. Your previous version is \
             still installed, but its services could not be restarted: {restart_error}. \
             Use Recovery to start them."
        ),
    }
}

/// Watch a confirmed quit that is waiting on a stop, and keep offering a way out.
///
/// `quit_after_stop` is consumed only by a `done` event saying the stop
/// succeeded, so any other outcome -- including no outcome -- leaves the app
/// running with the user's quit unanswered. This asks once per budget.
///
/// It loops. Asking once and then, on "Keep waiting", re-arming the flag and
/// returning meant the offer never came back: a stop that never confirmed sat
/// on "Winding down." for ever, and repeating the shortcut was no escape
/// either, because `request_quit` returns early once `quit_confirmed` is set
/// and `ExitRequested` refuses the exit in that state.
///
/// Written over its effects so the cycle can be tested without a 45 second
/// sleep, a window, or a daemon.
pub(crate) fn run_quit_watchdog(
    mut wait: impl FnMut(),
    still_waiting: impl Fn() -> bool,
    ask: impl Fn() -> bool,
    rearm: impl Fn(),
    leave: impl FnOnce(),
) {
    loop {
        wait();
        if !still_waiting() {
            return; // The stop finished and the app is already gone.
        }
        if ask() {
            leave();
            return;
        }
        // They chose to wait, so re-arm: a stop that lands later should still
        // complete the quit they originally asked for.
        rearm();
    }
}

/// Exit without stopping anything, for the cases where there is nothing to stop.
pub(crate) fn finish_quit(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    shell.quit_confirmed.store(true, Ordering::Release);
    let worker = app.clone();
    let exiting = app.clone();
    shell.shutdown.start(
        move || shut_down_gracefully(&worker),
        move || exiting.exit(0),
        QUIT_DAEMON_BUDGET,
    );
}

pub(crate) fn finish_quit_after_daemon(app: &AppHandle) {
    let worker = app.clone();
    let exiting = app.clone();
    app.state::<Shell>().shutdown.start(
        move || {
            // The daemon has already closed sharing and reaped services. Do
            // not send another release transaction while it flushes its reply.
            if wait_for_locald_exit(QUIT_DAEMON_GRACE_ATTEMPTS, "quitting").is_err() {
                leave_nothing_running(&worker);
            }
        },
        move || exiting.exit(0),
        QUIT_DAEMON_BUDGET,
    );
}

/// Quit has to mean quit.
///
/// Closing the window hides Lemma to the tray and everything keeps running --
/// that is deliberate, and it is how a person leaves Lemma working while they
/// do something else. Quitting is the other half of that bargain, and it was
/// not being honoured: the app exited and `lemma-locald` stayed up, supervising
/// Postgres, Redis, the backend, the Agent Host and a virtual machine, with no
/// window, no tray icon and nothing in the Dock. The only way to see it was
/// `ps`, and the only way to stop it was `kill`.
///
/// A background service is a fine thing to have. A background service with no
/// user interface is not one the user agreed to.
///
/// So this stops the daemon on the way out, using the same graceful-then-forced
/// path an app update uses -- the forced arm re-authenticates and matches the
/// packaged executable before it signals anything, so it can never reach a
/// daemon this app does not own.
/// Everything a quit owes the machine, done once and off the main thread.
///
/// Ordered: close any LAN/public exposure first, because that is the part the
/// daemon cannot infer from its own shutdown, then stop the daemon itself.
///
/// Idempotent by flag, not by luck. The confirmed path runs this on a worker
/// and then calls `app.exit(0)`, which lands on `RunEvent::Exit` -- and doing
/// it again there would put the whole wait back on the main thread, which is
/// the thing that made quitting hang.
pub(crate) fn shut_down_gracefully(app: &AppHandle) {
    if current_mode(app) == "local" {
        if let Err(error) = release_before_exit() {
            append_install_log(&format!("[quit] sharing could not be closed: {error}"));
        }
    }
    leave_nothing_running(app);
}

pub(crate) fn leave_nothing_running(app: &AppHandle) {
    // Drop this client first. The daemon broadcasts to connected clients while
    // it shuts down, and a writer belonging to a window that is going away is
    // one more thing that can block the exit.
    disconnect_locald(app);
    let outcome = connect_locald()
        .and_then(|connection| stop_locald(connection, "quitting", QUIT_DAEMON_GRACE_ATTEMPTS));
    match outcome {
        Ok(()) => append_install_log("[quit] the local service manager stopped"),
        // Not fatal, and deliberately not a dialog. The user has asked to
        // leave; trapping them behind a modal about a daemon is worse than the
        // daemon. But it goes in the log, because "Lemma is still running after
        // I quit" is otherwise unexplainable.
        Err(error) => append_install_log(&format!(
            "[quit] the local service manager could not be stopped: {error}"
        )),
    }
}

// An exit that did not stop the stack must still close any LAN or public
// exposure — `finish_quit`, and the second ⌘Q that leaves a wedged stop behind,
// both reach here. The daemon deliberately outlives the app, so it cannot infer
// this from its own shutdown.
pub(crate) fn release_before_exit() -> Result<(), String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let _ = sender.send(request_desktop_release());
    });
    receiver
        .recv_timeout(RELEASE_ON_EXIT_TIMEOUT)
        .map_err(|_| "timed out while stopping sharing".to_string())?
}

pub(crate) fn request_desktop_release() -> Result<(), String> {
    let mut connection = connect_locald()?;
    let id = format!("desktop-exit-release-{}", std::process::id());
    writeln!(
        connection.writer,
        "{}",
        json!({"v": 1, "cmd": "desktop.release", "id": id})
    )
    .map_err(|error| format!("could not request desktop release: {error}"))?;
    connection
        .writer
        .flush()
        .map_err(|error| format!("could not request desktop release: {error}"))?;

    loop {
        let mut line = String::new();
        let bytes = connection
            .reader
            .read_line(&mut line)
            .map_err(|error| format!("could not confirm desktop release: {error}"))?;
        if bytes == 0 {
            return Err("locald disconnected before confirming desktop release".into());
        }
        if line.len() > 1024 * 1024 {
            return Err("locald desktop release response exceeded 1 MiB".into());
        }
        let Ok(event) = serde_json::from_str::<Value>(line.trim_end()) else {
            continue;
        };
        if event.get("id").and_then(Value::as_str) != Some(id.as_str()) {
            continue;
        }
        match event.get("event").and_then(Value::as_str) {
            Some("done") if event.get("ok").and_then(Value::as_bool) == Some(true) => {
                return Ok(());
            }
            Some("done" | "error") => {
                return Err(event
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("locald could not stop sharing")
                    .to_string());
            }
            _ => {}
        }
    }
}
