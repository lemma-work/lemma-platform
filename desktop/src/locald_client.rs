use super::*;

pub(crate) fn install_locald_connection(app: &AppHandle, connection: LocaldConnection) {
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = Some(connection.writer);
    let handle = app.clone();
    std::thread::spawn(move || {
        // Bounded as the bytes arrive. `lines()` builds the whole line first,
        // so the 1 MiB check that used to sit here ran *after* the allocation
        // it was meant to prevent -- a daemon that sent no newline could make
        // this process allocate until it failed.
        let mut reader = connection.reader;
        loop {
            match ipc_read::bounded_line(&mut reader, 1024 * 1024) {
                Ok(Some(line)) => match serde_json::from_str::<Value>(&line) {
                    Ok(event) => handle_locald_event(&handle, &event),
                    Err(_) => emit_log(&handle, &line),
                },
                Ok(None) => break,
                Err(error) => {
                    emit_log(&handle, &format!("locald protocol error: {error}"));
                    break;
                }
            }
        }
        locald_gone(&handle);
    });
    // Ask once on connect so the tray reports real state instead of "checking…"
    // until something else happens to mention the Agent Host.
    let _ = send_to_locald(
        app,
        json!({"cmd": "agent-host.status", "id": operation_id("agent-host-initial")}),
    );
}

pub(crate) fn send_to_locald(app: &AppHandle, message: Value) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    let mut guard = shell.locald_writer.lock().unwrap();
    let writer = guard.as_mut().ok_or("lemma-locald is not connected")?;
    writeln!(writer, "{message}").map_err(|e| format!("locald write failed: {e}"))?;
    writer
        .flush()
        .map_err(|e| format!("locald flush failed: {e}"))
}

pub(crate) fn reserve_ui_operation(
    ui: &mut UiState,
    command: &str,
    id: &str,
) -> Result<(), String> {
    if !ui.active_operation_id.is_empty() && command != "shutdown-daemon" {
        return Err(LOCALD_BUSY.to_string());
    }
    if command == "shutdown-daemon" && !ui.active_operation_id.is_empty() {
        ui.completed_operation_ids
            .push(ui.active_operation_id.clone());
        if ui.completed_operation_ids.len() > 16 {
            ui.completed_operation_ids.remove(0);
        }
    }
    ui.active_operation_id = id.to_owned();
    Ok(())
}

pub(crate) fn send_local_operation(
    app: &AppHandle,
    mut request: Value,
    id: String,
) -> Result<(), String> {
    {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        reserve_ui_operation(&mut ui, request["cmd"].as_str().unwrap_or_default(), &id)?;
    }
    request["id"] = Value::String(id.clone());
    if let Err(error) = send_to_locald(app, request) {
        let shell: State<Shell> = app.state();
        let mut ui = shell.ui.lock().unwrap();
        if ui.active_operation_id == id {
            ui.active_operation_id.clear();
        }
        return Err(error);
    }
    Ok(())
}

pub(crate) fn locald_gone(app: &AppHandle) {
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = None;
    let snapshot = {
        let mut ui = shell.ui.lock().unwrap();
        if ui.running {
            ui.status = "Local service manager disconnected".into();
            ui.error = true;
            ui.error_code = "locald-disconnected".into();
            ui.running = false;
        }
        ui.active_operation_id.clear();
        ui.clone()
    };
    let _ = app.emit("lemma:state", snapshot);
    let _ = app.emit_to("control", "lemma:locald-disconnected", ());
    // The tray is driven from `handle_locald_event`, which by definition stops
    // arriving when the daemon does -- so the menu bar kept reading
    // "Lemma: running" and "Agent Host: connected" indefinitely after the stack
    // had gone, which is exactly when someone looks at it.
    refresh_tray_status(app);
    refresh_agent_host_tray(app, &json!({"available": false}));
    if current_mode(app) == "local" && app.get_webview("control").is_none() {
        show_splash(app);
    }
}

pub(crate) fn emit_log(app: &AppHandle, line: &str) {
    if !line.is_empty() {
        let _ = app.emit("lemma:log", line.to_string());
    }
}

/// Ask locald something and wait for its answer.
///
/// The shared client connection is a broadcast stream: replies arrive as events
/// with no way to hand one back to a specific `invoke`. Commands whose *result*
/// is the point — a model list, an applied profile — take their own short-lived
/// connection and read until their own id comes back, so the caller gets a
/// value and a real error message instead of having to guess from a poll.
pub(crate) fn locald_request(command: Value, timeout: Duration) -> Result<Value, String> {
    let id = command
        .get("id")
        .and_then(Value::as_str)
        .ok_or("request needs an id")?;
    let mut connection = connect_locald_with_mode(true)?;
    writeln!(connection.writer, "{command}")
        .and_then(|_| connection.writer.flush())
        .map_err(|error| format!("could not reach Lemma: {error}"))?;
    ipc_read::response(&mut connection.reader, id, timeout)
}

pub(crate) fn agent_host_request(app: &AppHandle, command: Value) -> Result<(), String> {
    let response = locald_request(command, Duration::from_secs(190))?;
    if let Some(status) = response.get("agent_host").filter(|value| value.is_object()) {
        let shell: State<Shell> = app.state();
        *shell.agent_host_status.lock().unwrap() = Some(status.clone());
        refresh_agent_host_tray(app, status);
    }
    Ok(())
}
