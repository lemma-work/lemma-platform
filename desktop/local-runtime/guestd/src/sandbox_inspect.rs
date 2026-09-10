//! Reading the engine's view of a container back into ours.

use super::*;

/// Did this container run and stop, as opposed to never having started?
///
/// Read from the fields nerdctl does fill when `State.Status` is absent: an
/// exit code, or a finish timestamp.
pub(crate) fn container_has_exited(state: &serde_json::Map<String, Value>) -> bool {
    state.get("ExitCode").and_then(Value::as_i64).is_some()
        || state
            .get("FinishedAt")
            .and_then(Value::as_str)
            .is_some_and(|value| !value.trim().is_empty())
}

pub(crate) fn snapshot_from_inspect(
    sandbox_id: &str,
    inspect: &serde_json::Map<String, Value>,
    endpoint_host: &str,
) -> Result<Value, GuestError> {
    let provider_id = inspect
        .get("Id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| GuestError::engine("inspect response omitted container ID"))?;
    let state = inspect.get("State").and_then(Value::as_object);
    let running = state
        .and_then(|value| value.get("Running"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let state_text = state
        .and_then(|value| value.get("Status"))
        .and_then(Value::as_str)
        .unwrap_or_default();
    let lifecycle = if running {
        "RUNNING"
    } else if matches!(state_text, "created" | "restarting") {
        "CREATING"
    } else if matches!(state_text, "exited" | "stopped" | "removing" | "paused") {
        // `paused` is here defensively: nothing in Lemma pauses a sandbox, and
        // if something did it is suspended rather than faulted. `dead` is
        // deliberately *not* here -- a container the engine could not clean up
        // is a fault, and calling it the ordinary resting state of an idle
        // workspace would hide exactly the case worth seeing.
        "STOPPED"
    } else if state_text.is_empty() && state.is_some_and(container_has_exited) {
        // nerdctl does not always fill `State.Status`. A container that is not
        // running and carries an exit code has stopped -- which is the ordinary
        // end of an idle release, not a fault. Reporting it as ERROR made the
        // most common resting state of a workspace look like a broken one.
        "STOPPED"
    } else {
        "ERROR"
    };
    let labels = inspect
        .get("Config")
        .and_then(Value::as_object)
        .and_then(|config| config.get("Labels"))
        .and_then(Value::as_object);
    let workload_kind = labels
        .and_then(|value| value.get("lemma.work/workload-kind"))
        .and_then(Value::as_str)
        .ok_or_else(|| GuestError::engine("sandbox workload label is missing"))?;
    let apps = match workload_kind {
        "workspace" => workspace_apps(),
        "function" => function_apps(),
        _ => return Err(GuestError::engine("sandbox workload label is invalid")),
    };
    let image = labels
        .and_then(|value| value.get("lemma.work/image-ref"))
        .and_then(Value::as_str)
        .ok_or_else(|| GuestError::engine("sandbox image label is missing"))?;
    let metadata = labels
        .and_then(|value| value.get("lemma.work/metadata"))
        .and_then(Value::as_str)
        .ok_or_else(|| GuestError::engine("sandbox metadata label is missing"))
        .and_then(|encoded| {
            serde_json::from_str::<BTreeMap<String, String>>(encoded)
                .map_err(|_| GuestError::engine("sandbox metadata label is invalid"))
        })?;
    let ports = inspect
        .get("NetworkSettings")
        .and_then(Value::as_object)
        .and_then(|network| network.get("Ports"))
        .and_then(Value::as_object);
    let mut statuses = serde_json::Map::new();
    for app in &apps {
        let host_port = ports.and_then(|value| mapped_port(value, app.port));
        statuses.insert(
            app.name.clone(),
            json!({
                "name": app.name,
                "public_slug": app.public_slug,
                "port": app.port,
                "ready": running && host_port.is_some(),
                "private_url": host_port.map(|port| format!("http://{endpoint_host}:{port}")),
            }),
        );
    }
    let runtime_url = statuses
        .get("runtime")
        .and_then(|value| value.get("private_url"))
        .cloned()
        .unwrap_or(Value::Null);
    let ready = running
        && apps
            .iter()
            .filter(|app| app.startup == "eager")
            .all(|app| statuses[&app.name]["ready"] == true);
    Ok(json!({
        "provider_id": provider_id,
        "image": image,
        "metadata": metadata,
        "status": {
            "id": sandbox_id,
            "ready": ready,
            "status": lifecycle,
            "runtime_url": runtime_url,
            "pod_ip": if running { Value::String(endpoint_host.into()) } else { Value::Null },
            "apps": statuses,
        }
    }))
}

pub(crate) fn mapped_port(
    ports: &serde_json::Map<String, Value>,
    container_port: u16,
) -> Option<u16> {
    ports
        .get(&format!("{container_port}/tcp"))
        .and_then(Value::as_array)
        .and_then(|bindings| bindings.first())
        .and_then(Value::as_object)
        .and_then(|binding| binding.get("HostPort"))
        .and_then(Value::as_str)
        .and_then(|port| port.parse().ok())
}

pub(crate) fn eager_apps_healthy(snapshot: &Value, apps: &[AppSpec]) -> bool {
    apps.iter().filter(|app| app.startup == "eager").all(|app| {
        snapshot["status"]["apps"][&app.name]["private_url"]
            .as_str()
            .map(|base| {
                let path = if app.health_path.starts_with('/') {
                    app.health_path.clone()
                } else {
                    format!("/{}", app.health_path)
                };
                probe_http(&format!("{}{path}", base.trim_end_matches('/'))).is_ok()
            })
            .unwrap_or(false)
    })
}

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn sandbox_diagnostics(&self, value: Value) -> Result<Value, GuestError> {
        let sandbox_id = required_string(&value, "sandbox_id")?;
        validate_sandbox_id(&sandbox_id)?;
        let container = container_name(&sandbox_id);
        let inspect = self
            .inspect_raw(&container)?
            .ok_or_else(GuestError::not_found)?;
        let state = inspect.get("State").and_then(Value::as_object);
        let config = inspect.get("Config").and_then(Value::as_object);
        let text = |name: &str| {
            state
                .and_then(|value| value.get(name))
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .map(redact_engine_error)
        };
        Ok(json!({
            "sandbox_id": sandbox_id,
            "process": {
                "path": inspect.get("Path").and_then(Value::as_str),
                "args": inspect.get("Args").and_then(Value::as_array),
                "entrypoint": config
                    .and_then(|value| value.get("Entrypoint"))
                    .and_then(Value::as_array),
                "cmd": config
                    .and_then(|value| value.get("Cmd"))
                    .and_then(Value::as_array),
            },
            "state": {
                "status": text("Status"),
                "running": state
                    .and_then(|value| value.get("Running"))
                    .and_then(Value::as_bool),
                "exit_code": state
                    .and_then(|value| value.get("ExitCode"))
                    .and_then(Value::as_i64),
                "oom_killed": state
                    .and_then(|value| value.get("OOMKilled"))
                    .and_then(Value::as_bool),
                "error": text("Error"),
                "started_at": text("StartedAt"),
                "finished_at": text("FinishedAt"),
            },
            "last_log": self.container_log_summary(&container),
        }))
    }
}
