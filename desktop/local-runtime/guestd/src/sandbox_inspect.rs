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

/// Whether an app answers on a host and port.
///
/// Taken as an argument rather than called directly so a unit test can decide
/// the answer. The real probe opens a TCP connection, and fixtures here map
/// ports like 49152-49154, which sit inside Linux's ephemeral range -- so a
/// listener another test had just been assigned could answer a probe meant for
/// nothing, and a probe meant for a listener could queue behind it. That made
/// readiness assertions pass on macOS and fail on Linux for reasons that had
/// nothing to do with the code under test.
pub(crate) type AppProbe<'a> = &'a dyn Fn(&str, u16, &str) -> bool;

pub(crate) fn snapshot_from_inspect(
    sandbox_id: &str,
    inspect: &serde_json::Map<String, Value>,
    endpoint_host: &str,
) -> Result<Value, GuestError> {
    snapshot_from_inspect_with(sandbox_id, inspect, endpoint_host, &app_answers)
}

pub(crate) fn snapshot_from_inspect_with(
    sandbox_id: &str,
    inspect: &serde_json::Map<String, Value>,
    endpoint_host: &str,
    probe: AppProbe<'_>,
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
    // What the caller declared when this container was created, read back off
    // the container. The compiled-in lists below are the fallback for one made
    // before that label existed -- and the reason the label exists: they are a
    // copy of a list the backend owns, and the copy was missing the browser
    // relay, so every snapshot of a live sandbox said port 4850 was not served
    // while the container was serving it.
    let declared = labels
        .and_then(|value| value.get("lemma.work/apps"))
        .and_then(Value::as_str)
        .and_then(|encoded| serde_json::from_str::<Vec<AppSpec>>(encoded).ok())
        .filter(|apps| validate_apps(apps).is_ok());
    // The kind is still checked when the label is present: an unrecognised one
    // is a container this guest did not create, and answering for it at all is
    // the mistake.
    let fallback = match workload_kind {
        "workspace" => workspace_apps(),
        "function" => function_apps(),
        _ => return Err(GuestError::engine("sandbox workload label is invalid")),
    };
    let apps = declared.unwrap_or(fallback);
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
    // Read back so a re-ensure can tell whether the running container still
    // has the grants being asked for (`existing_container_verdict`). A
    // container made before the label existed had the alias -- that was the
    // only behaviour then -- so absent reads as `true`.
    let host_access = labels
        .and_then(|value| value.get("lemma.work/host-access"))
        .and_then(Value::as_str)
        .is_none_or(|value| value != "false");
    // The relay was never granted before its label existed.
    let host_loopback = labels
        .and_then(|value| value.get("lemma.work/host-loopback"))
        .and_then(Value::as_str)
        .is_some_and(|value| value == "true");
    // Which hardening this container was made with; see
    // `SANDBOX_HARDENING_VERSION`. No label is a container from before any.
    let hardening = labels
        .and_then(|value| value.get("lemma.work/hardening"))
        .and_then(Value::as_str)
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(0);
    let ports = inspect
        .get("NetworkSettings")
        .and_then(Value::as_object)
        .and_then(|network| network.get("Ports"))
        .and_then(Value::as_object);
    let mut statuses = serde_json::Map::new();
    for app in &apps {
        let host_port = ports.and_then(|value| mapped_port(value, app.port));
        // Published is what the engine can tell us: the container runs and a
        // port is mapped. It is not the same as answering, and reporting it as
        // `ready` is what let the guest promise a browser relay that refused
        // every connection.
        let published = running && host_port.is_some();
        // Every app is probed, eager and lazy alike. Lazy is the case this
        // exists for: the browser and its relay were reported `ready: true`
        // from a mapped port while both refused every connection, and the
        // backend dialled an endpoint the guest had just promised was good.
        // Probing a lazy app that has not started costs a connection refused,
        // which on a container on this host is immediate.
        let answering =
            published && host_port.is_some_and(|port| probe(endpoint_host, port, &app.health_path));
        statuses.insert(
            app.name.clone(),
            json!({
                "name": app.name,
                "public_slug": app.public_slug,
                "port": app.port,
                // What the engine knows: it is running and a port is mapped.
                "published": published,
                // What was asked: it answered its declared health path.
                "ready": answering,
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
        "grants": {"host_access": host_access, "host_loopback": host_loopback},
        "hardening": hardening,
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

/// Whether an app answers its health path, through the guest's one HTTP prober.
///
/// `probe_http` is what readiness has always used: any status below 500 is a
/// server that is serving -- a 401 from the runtime, which wants a credential
/// guestd does not hold, included -- and a 5xx is one that is not.
pub(crate) fn app_answers(host: &str, port: u16, health_path: &str) -> bool {
    let path = health_path.strip_prefix('/').unwrap_or(health_path);
    probe_http(&format!("http://{host}:{port}/{path}")).is_ok()
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
