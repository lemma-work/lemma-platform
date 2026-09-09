//! One request to the guest agent, and how long it is given.

use super::*;

pub(crate) fn guest_request_budget(operation: &str) -> Duration {
    match operation {
        "system.shutdown" => Duration::from_secs(8),
        "health" | "core.sandbox_images_status" => Duration::from_secs(5),
        // Pulling images is the one operation whose length is set by the
        // user's connection rather than by the guest. A first install fetches
        // roughly a gigabyte, and eight minutes is a limit on a slow download
        // rather than on a stuck one -- on a real Windows machine it failed
        // every attempt while making progress on every attempt.
        //
        // Longer than the guest's own `ENGINE_PULL_TIMEOUT`, deliberately. If
        // this expired first the guest would keep pulling into a request
        // nobody is waiting on, and the next attempt would contend with it.
        "core.images" | "core.sandbox_images" => Duration::from_secs(75 * 60),
        // Collected on a failure path, so the wait is added to a start that
        // has already gone wrong. The guest bounds its own collection well
        // inside this; the margin is for a guest slow enough to need it.
        "diagnostics.guest" => Duration::from_secs(45),
        _ => Duration::from_secs(8 * 60),
    }
}

pub(crate) fn cache_repair_required(error: &io::Error) -> bool {
    error
        .to_string()
        .contains("container cache repair required")
}

impl ManagedRuntime {
    pub fn request(&self, operation: &str, parameters: Value) -> io::Result<Value> {
        self.request_cancellable(
            operation,
            parameters,
            lemma_desktop_process::Cancellation::default(),
        )
    }

    pub fn request_cancellable(
        &self,
        operation: &str,
        parameters: Value,
        cancellation: lemma_desktop_process::Cancellation,
    ) -> io::Result<Value> {
        if operation.is_empty()
            || !operation
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte == b'.' || byte == b'_')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid guest operation",
            ));
        }
        #[cfg(target_os = "macos")]
        if operation != "system.shutdown" && !operation.starts_with("diagnostics.") {
            self.check_guest_kernel()?;
        }
        let request = json!({
            "version": 1,
            "operation": operation,
            "parameters": parameters,
        });
        let mut encoded = serde_json::to_vec(&request)?;
        encoded.push(b'\n');
        let mut command = Command::new(&self.config.bridge_executable);
        command
            .no_console_window()
            .arg("request")
            .env("LEMMA_GUEST_CAPABILITY_FILE", &self.capability_file)
            .env("LEMMA_GUEST_CONTROL_SOCKET", &self.control_socket)
            .env("LEMMA_WSL_DISTRIBUTION", &self.config.wsl_distribution);
        let budget = guest_request_budget(operation);
        let output = lemma_desktop_process::run_with_input_cancellable(
            command,
            encoded,
            budget,
            MAX_RESPONSE_BYTES,
            cancellation,
        );
        #[cfg(target_os = "macos")]
        if operation != "system.shutdown" && !operation.starts_with("diagnostics.") {
            self.check_guest_kernel()?;
        }
        let output = output.map_err(|error| match error {
            lemma_desktop_process::SetupProcessError::TimedOut => io::Error::new(
                io::ErrorKind::TimedOut,
                "runtime bridge exceeded its request deadline",
            ),
            lemma_desktop_process::SetupProcessError::OutputLimit => io::Error::new(
                io::ErrorKind::InvalidData,
                "runtime bridge exceeded its output limit",
            ),
            lemma_desktop_process::SetupProcessError::Io(error) => error,
            lemma_desktop_process::SetupProcessError::Cancelled => {
                io::Error::new(io::ErrorKind::Interrupted, "runtime command was cancelled")
            }
        })?;
        if output.stdout.is_empty() {
            let detail = first_diagnostic(&output.stderr, "private guest did not respond");
            return Err(io::Error::new(
                io::ErrorKind::ConnectionRefused,
                format!("could not reach Lemma's private runtime: {detail}"),
            ));
        }
        let response: Value = serde_json::from_slice(&output.stdout).map_err(|error| {
            let detail = String::from_utf8_lossy(&output.stderr);
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "runtime bridge returned invalid JSON ({error}): {}",
                    detail.lines().next().unwrap_or("no diagnostic")
                ),
            )
        })?;
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            let error = response.get("error").and_then(Value::as_object);
            return Err(io::Error::other(
                error
                    .and_then(|value| value.get("message"))
                    .and_then(Value::as_str)
                    .unwrap_or("managed guest request failed"),
            ));
        }
        response
            .get("result")
            .cloned()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "guest omitted result"))
    }
}
