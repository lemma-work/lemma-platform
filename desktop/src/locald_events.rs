use super::*;

pub(crate) fn locald_event_operation_id(event: &Value) -> Option<&str> {
    event
        .get("operation_id")
        .and_then(Value::as_str)
        .or_else(|| {
            matches!(event["event"].as_str(), Some("ack" | "done" | "error"))
                .then(|| event.get("id").and_then(Value::as_str))
                .flatten()
        })
}

pub(crate) fn event_applies_during_shutdown(event: &Value) -> bool {
    match event["event"].as_str().unwrap_or_default() {
        "log" => true,
        "status" | "state" | "ready" | "runtime.prepared" => false,
        _ => locald_event_operation_id(event).is_some(),
    }
}

/// Fold one daemon event into the shell's view of the world.
///
/// Pulled out of `handle_locald_event`, which was 391 lines mixing this with
/// window navigation, tray refreshes and quit completion — and had no test at
/// all, on the path that decides what every screen shows. Everything here is a
/// function of the previous state and the event; what the caller must *do*
/// comes back as [`EventOutcome`] rather than happening in the middle.
///
/// `log` is not handled here: it is the one kind that only forwards, and it
/// needs no state, so the caller takes it before acquiring the lock.
pub(crate) fn apply_locald_event(ui: &mut UiState, kind: &str, event: &Value) -> EventOutcome {
    let mut outcome = EventOutcome::default();
    let event_operation_id = locald_event_operation_id(event);
    let ui = &mut *ui;
    match kind {
        "phase" => {
            ui.phase = event["label"].as_str().unwrap_or_default().into();
            ui.phase_key = event["key"].as_str().unwrap_or_default().into();
            ui.progress = event["progress"].as_u64().unwrap_or(0);
            ui.eta_seconds = event["eta_s"].as_u64();
            ui.downloaded_bytes = None;
            ui.total_bytes = None;
            ui.throughput_bytes_per_second = None;
            ui.setup = event["setup"].as_bool().unwrap_or(ui.setup);
            if let Some(component) = event["component"].as_str() {
                ui.component = component.into();
            }
            if let Some(source) = event["log_source"].as_str() {
                ui.log_source = source.into();
            }
            let detail = event["detail"].as_str().unwrap_or_default();
            ui.status = if detail.is_empty() {
                ui.phase.clone()
            } else {
                format!("{}: {}", ui.phase, detail)
            };
            ui.ready = false;
            ui.error = ui.phase_key == "error";
            if !ui.error {
                ui.error_code.clear();
            }
        }
        "state" => {
            ui.running = event["running"].as_bool().unwrap_or(false);
            ui.ready = event["ready"].as_bool().unwrap_or(false);
            let event_status = event["status"].as_str().unwrap_or_default();
            let event_is_error = event_status == "error";
            let keep_actionable_error =
                is_actionable_runtime_error(&ui.error_code) && !ui.ready && !event_is_error;
            ui.error = event_is_error || keep_actionable_error;
            if !ui.error {
                ui.error_code.clear();
            }
            if event_status == "stopped" && !ui.error {
                ui.phase = "Stopped".into();
                ui.phase_key = "stopped".into();
                ui.progress = 0;
                ui.eta_seconds = None;
                ui.downloaded_bytes = None;
                ui.total_bytes = None;
                ui.throughput_bytes_per_second = None;
                ui.status = "Local services are stopped".into();
            }
        }
        "status" => {
            ui.running = event["running"].as_bool().unwrap_or(ui.running);
            ui.ready = event["ready"].as_bool().unwrap_or(ui.ready);
            let event_status = event["status"].as_str().unwrap_or_default();
            let preserve_inflight_phase = should_preserve_inflight_phase(
                &ui.active_operation_id,
                &ui.phase_key,
                event_status,
            );
            let event_is_error = event_status == "error";
            let keep_actionable_error =
                is_actionable_runtime_error(&ui.error_code) && !ui.ready && !event_is_error;
            let keep_terminal_error =
                ui.error && !ui.ready && event_status == "stopped" && !event_is_error;
            ui.error = event_is_error || keep_actionable_error || keep_terminal_error;
            if !ui.error {
                ui.error_code.clear();
            }
            if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
                if trusted_workspace_urls(url, api_url) {
                    ui.url = url.to_string();
                    ui.api_url = api_url.to_string();
                }
            }
            if !keep_actionable_error && !keep_terminal_error && !preserve_inflight_phase {
                let phase = event.get("phase").and_then(Value::as_object);
                if event_status == "stopped" && !ui.error {
                    // Lifecycle state wins over persisted progress. Older
                    // daemons may legitimately report stopped while their
                    // last phase still says ready/100%.
                    ui.phase = "Stopped".into();
                    ui.phase_key = "stopped".into();
                    ui.progress = 0;
                    ui.eta_seconds = None;
                    ui.downloaded_bytes = None;
                    ui.total_bytes = None;
                    ui.throughput_bytes_per_second = None;
                    ui.status = "Local services are stopped".into();
                } else if let Some(phase) = phase {
                    ui.phase = phase
                        .get("label")
                        .and_then(Value::as_str)
                        .unwrap_or(&ui.phase)
                        .to_string();
                    ui.phase_key = phase
                        .get("key")
                        .and_then(Value::as_str)
                        .unwrap_or(&ui.phase_key)
                        .to_string();
                    ui.progress = phase
                        .get("progress")
                        .and_then(Value::as_u64)
                        .unwrap_or(ui.progress);
                    ui.downloaded_bytes = None;
                    ui.total_bytes = None;
                    ui.throughput_bytes_per_second = None;
                    let detail = phase.get("detail").and_then(Value::as_str).unwrap_or("");
                    ui.status = if detail.is_empty() {
                        ui.phase.clone()
                    } else {
                        format!("{}: {detail}", ui.phase)
                    };
                }
            }
        }
        "ready" => {
            if !ui.ready {
                launch_trace("daemon reported ready");
            }
            ui.ready = true;
            ui.running = true;
            ui.error = false;
            ui.error_code.clear();
            ui.downloaded_bytes = None;
            ui.total_bytes = None;
            ui.throughput_bytes_per_second = None;
            // Main, API, built-app, and workspace-app hosts all live below
            // the reserved lemma.localhost loopback cookie boundary.
            if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
                if trusted_workspace_urls(url, api_url) {
                    ui.url = url.to_string();
                    ui.api_url = api_url.to_string();
                    // Record what is serving, and under which generation,
                    // so the next launch can skip straight to it.
                    //
                    // On a worker, because this writes the config with two
                    // fsyncs and we are holding `shell.ui` -- a lock the main
                    // thread takes in `navigation_context` (on every
                    // navigation, subframes included), `get_state`,
                    // `current_mode`, `refresh_tray_status` and
                    // `quit_impact`. Holding it across a disk sync stalled
                    // WebKit's navigation delegate, worst exactly when the
                    // disk is busy unpacking a runtime. The resume target is
                    // advisory, so late is fine and lost is survivable.
                    let (url, api_url, generation) = (
                        url.to_string(),
                        api_url.to_string(),
                        event["runtime_generation"]
                            .as_str()
                            .unwrap_or_default()
                            .to_string(),
                    );
                    std::thread::spawn(move || {
                        write_resume_target(&url, &api_url, &generation);
                    });
                }
            }
            // Navigation is not decided here. The tail of this
            // function owns ready -> workspace, for every event kind
            // that can carry readiness; deciding it in two places is
            // how one of them ended up never running.
        }
        "sharing.changed" => {
            if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
                if trusted_workspace_urls(url, api_url) {
                    ui.url = url.to_owned();
                    ui.api_url = api_url.to_owned();
                }
            }
        }
        "error" => {
            let code = event["code"].as_str().unwrap_or_default();
            if code == "busy" {
                // Every authenticated desktop client already receives the
                // in-flight operation's broadcast progress. A repeated
                // Start click is therefore informational, not a failure.
                ui.error = false;
                ui.error_code.clear();
                ui.status = if ui.phase.is_empty() {
                    "Lemma is already working on that operation…".into()
                } else {
                    format!("{} is still in progress…", ui.phase)
                };
                if event_operation_id.is_some_and(|id| id == ui.active_operation_id) {
                    ui.active_operation_id.clear();
                }
            } else if code.starts_with("sharing-") {
                // Sharing failures are shown inside Local settings. They
                // must not replace an otherwise healthy workspace with the
                // startup error screen.
                ui.error = false;
                ui.error_code.clear();
            } else {
                ui.error = true;
                ui.error_code = code.into();
                ui.status = event["message"].as_str().unwrap_or("startup failed").into();
                if let Some(component) = event["component"].as_str() {
                    ui.component = component.into();
                }
                if let Some(source) = event["log_source"].as_str() {
                    ui.log_source = source.into();
                }
            }
        }
        "sandbox-images" => {
            // Deliberately touches nothing else. This runs after the
            // workspace is up, so writing `phase`/`ready` here would send
            // an app the user is already working in back to the splash to
            // report a download they never asked about.
            ui.sandbox_images = event["state"].as_str().unwrap_or_default().into();
            ui.sandbox_images_detail = event["detail"].as_str().unwrap_or_default().into();
        }
        "runtime.prepared" => {
            let ready = event["ready"].as_bool().unwrap_or(false);
            let reboot_required = event["reboot_required"].as_bool().unwrap_or(!ready);
            ui.ready = false;
            ui.running = false;
            ui.phase = "Preparing Windows".into();
            ui.phase_key = "runtime".into();
            if ready {
                ui.error = false;
                ui.error_code.clear();
                ui.status = "Windows runtime is ready. Starting Lemma…".into();
                outcome.start_after_prepare = ui.mode == "local";
            } else if reboot_required {
                ui.error = true;
                ui.error_code = "wsl-reboot-required".into();
                ui.status =
                        "Restart Windows to finish setup, then reopen Lemma; setup will continue automatically"
                            .into();
            }
        }
        "done" if event_operation_id.is_some_and(|id| id == ui.active_operation_id) => {
            let completed_operation_id = ui.active_operation_id.clone();
            ui.completed_operation_ids.push(completed_operation_id);
            if ui.completed_operation_ids.len() > 16 {
                ui.completed_operation_ids.remove(0);
            }
            ui.active_operation_id.clear();
        }
        _ => {}
    }
    if ui.mode == "local" && ui.ready && !trusted_workspace_urls(&ui.url, &ui.api_url) {
        ui.ready = false;
        ui.running = false;
        ui.error = true;
        ui.error_code = "untrusted-workspace-origin".into();
        ui.phase = "Local services need attention".into();
        ui.phase_key = "error".into();
        ui.progress = 0;
        ui.status = "locald did not provide an authenticated, isolated workspace origin".into();
    }
    if ui.ready || !ui.error {
        ui.terminal_recovery_pending = false;
    }
    let schedule_terminal_recovery = matches!(kind, "state" | "status")
        && ui.error
        && !ui.ready
        && !ui.terminal_recovery_pending;
    if schedule_terminal_recovery {
        ui.terminal_recovery_pending = true;
    }
    outcome.schedule_terminal_recovery = schedule_terminal_recovery;
    outcome
}

/// What to do with an event that names the operation it belongs to.
///
/// The whole of a decision that decides what somebody watching the splash
/// sees, and the reason it is a function: `handle_locald_event` needs an
/// `AppHandle` and a Tauri runtime, so none of this was reachable from a test
/// and every case below was only ever exercised by using the app.
///
/// The daemon serves one operation at a time but several surfaces can ask, and
/// their replies interleave. Showing another operation's progress on the
/// splash is not cosmetic: its phases and its errors are about work the person
/// in front of it did not start.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum EventAdmission {
    /// It belongs to the operation already on screen.
    Apply,
    /// Nothing is on screen and this operation has not finished, so it becomes
    /// the one being shown.
    Adopt,
    /// Another operation's, or one whose completion has already been shown.
    /// The second is what stops a late straggler from reopening a finished
    /// run's progress after the splash has moved on.
    Ignore,
}

pub(crate) fn admit_locald_event(
    active: &str,
    completed: &[String],
    event: &str,
) -> EventAdmission {
    if !active.is_empty() {
        return if active == event {
            EventAdmission::Apply
        } else {
            EventAdmission::Ignore
        };
    }
    if completed.iter().any(|finished| finished == event) {
        return EventAdmission::Ignore;
    }
    EventAdmission::Adopt
}

pub(crate) fn handle_locald_event(app: &AppHandle, event: &Value) {
    if std::env::var("LEMMA_DESKTOP_DEBUG").as_deref() == Ok("1") {
        eprintln!("[locald] {event}");
    }
    let shell: State<Shell> = app.state();
    let kind = event["event"].as_str().unwrap_or_default();
    if shell.quit_after_stop.load(Ordering::Acquire) && !event_applies_during_shutdown(event) {
        return;
    }
    let event_operation_id = locald_event_operation_id(event);
    if let Some(event_operation_id) = event_operation_id {
        let mut ui = shell.ui.lock().unwrap();
        match admit_locald_event(
            &ui.active_operation_id,
            &ui.completed_operation_ids,
            event_operation_id,
        ) {
            EventAdmission::Ignore => return,
            EventAdmission::Adopt => ui.active_operation_id = event_operation_id.to_owned(),
            EventAdmission::Apply => {}
        }
    }
    let _ = app.emit_to("control", "lemma:locald-event", event.clone());
    // Every reply that carries Agent Host state refreshes the tray, so a change
    // made in one surface shows in the others without anyone polling.
    if let Some(status) = event.get("agent_host").filter(|value| value.is_object()) {
        *shell.agent_host_status.lock().unwrap() = Some(status.clone());
        refresh_agent_host_tray(app, status);
    }
    // Same reason, for sharing: several events carry it, and Quit needs the last
    // known answer without asking.
    if let Some(mode) = event
        .get("sharing")
        .and_then(|sharing| sharing.get("mode"))
        .and_then(Value::as_str)
    {
        *shell.sharing_mode.lock().unwrap() = Some(mode.to_owned());
    }

    if kind == "log" {
        emit_log(app, event["line"].as_str().unwrap_or_default());
        return;
    }
    let (snapshot, outcome) = {
        let mut ui = shell.ui.lock().unwrap();
        let outcome = apply_locald_event(&mut ui, kind, event);
        (ui.clone(), outcome)
    };
    let schedule_terminal_recovery = outcome.schedule_terminal_recovery;
    let start_after_prepare = outcome.start_after_prepare;

    let ready_workspace_url = (matches!(kind, "ready" | "state" | "status")
        && snapshot.mode == "local"
        && snapshot.ready
        && !snapshot.error)
        .then(|| snapshot.url.clone());
    let _ = app.emit("lemma:state", snapshot);
    refresh_tray_status(app);
    if let Some(url) = ready_workspace_url {
        if main_window_needs_workspace(app, &url) {
            // Prefer the route the last session ended on. Opening the root
            // instead means loading the app once to authenticate and resolve
            // the last pod, then loading it again at the pod it resolved to.
            // A cold first run has no resume target and no account, so the
            // root would load once to discover that, redirect to signup, and
            // load again. Go straight there: one page load, and the first
            // screen is deterministic instead of depending on a client redirect.
            let target = read_resume_target()
                .filter(|target| target.url == url)
                .map(|target| resume_entry_url(&target))
                .unwrap_or_else(|| local_auth_url_returning_to(&url, "signup", "/"));
            let _ = open_app_window(app, &target);
        }
    }
    if kind == "sharing.changed" {
        if let (Some(url), Some(api_url)) = (event["url"].as_str(), event["api_url"].as_str()) {
            if trusted_workspace_urls(url, api_url) {
                let _ = open_app_window(app, url);
            }
        }
    }
    let quit_after_stop = kind == "done"
        && event["cmd"].as_str() == Some("shutdown-daemon")
        && event["ok"].as_bool() == Some(true)
        && shell.quit_after_stop.swap(false, Ordering::AcqRel);
    if quit_after_stop {
        finish_quit_after_daemon(app);
        return;
    }
    if schedule_terminal_recovery {
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(8));
            let should_recover = {
                let shell: State<Shell> = app.state();
                let ui = shell.ui.lock().unwrap();
                ui.terminal_recovery_pending && ui.error && !ui.ready && ui.mode == "local"
            };
            if should_recover {
                show_splash(&app);
            }
        });
    }
    if start_after_prepare {
        let app = app.clone();
        std::thread::spawn(move || {
            // The daemon releases its single-operation guard immediately after
            // publishing runtime.prepared. Avoid racing the follow-up start.
            std::thread::sleep(Duration::from_millis(250));
            let _ = send_local_operation(
                &app,
                json!({"cmd":"start"}),
                operation_id("shell-start-after-runtime-prepare"),
            );
        });
    }
    // Do not navigate an already-open workspace back to the installer for
    // transient component events. The splash is already visible during setup;
    // a lost daemon uses locald_gone(), the terminal recovery path.
}

pub(crate) fn is_actionable_runtime_error(code: &str) -> bool {
    matches!(
        code,
        "wsl-required" | "wsl-reboot-required" | "wsl-setup-denied"
    )
}

pub(crate) fn should_preserve_inflight_phase(
    active_operation_id: &str,
    phase_key: &str,
    status: &str,
) -> bool {
    !active_operation_id.is_empty()
        && status == "stopped"
        && !matches!(phase_key, "" | "boot" | "stopped" | "ready" | "error")
}
