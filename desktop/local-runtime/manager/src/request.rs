//! One request to the guest agent, and how long it is given.

use super::*;

/// How the host reaches the guest, which decides who can own a long download.
///
/// macOS runs one resident guestd behind a vsock channel. Windows runs
/// `wsl.exe --exec lemma-guestd request` and gets a process that ends with its
/// reply -- so work that outlives a request has nowhere to live there, and the
/// request has to be given room to do it instead.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GuestTransport {
    Resident,
    PerRequest,
}

impl GuestTransport {
    pub(crate) fn of_this_host() -> Self {
        // `cfg!` rather than `#[cfg]` so both arms are compiled and both are
        // reachable from a test on either platform.
        if cfg!(target_os = "macos") {
            Self::Resident
        } else {
            Self::PerRequest
        }
    }
}

/// The guest's own worst case for a stop, restated here.
///
/// `stop_all_containers` gives a sandbox one second and a data service fifteen,
/// and `nerdctl stop` works through its arguments one at a time -- so with the
/// sandbox ceiling at sixteen the guest can legitimately spend
/// 30 x 1 + 3 x 15 = 75 seconds. The budget below has to exceed that.
///
/// It was eight seconds. Whichever container was still stopping when that
/// expired had the guest terminated underneath it, and the container most
/// likely to still be stopping is the one that takes longest, which is the
/// database. `guestd::GUEST_STOP_WORST_CASE_SECONDS` is the same number on the
/// other side; the two are compiled into different binaries, so this comment is
/// the link and the test below is the check.
pub(crate) const GUEST_STOP_WORST_CASE_SECONDS: u64 = 75;

pub(crate) fn guest_request_budget(operation: &str, transport: GuestTransport) -> Duration {
    match operation {
        // A backstop against a hang, not a target. A stop on an ordinary
        // installation is a second or two: nothing waits out this budget
        // unless something is genuinely stuck, and cutting it short is how a
        // busy database gets killed mid-checkpoint.
        "system.shutdown" => Duration::from_secs(GUEST_STOP_WORST_CASE_SECONDS + 15),
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
        // Same reason, one transport at a time. A per-request guest cannot
        // download in the background -- its process ends with the reply -- so
        // a `sandbox.ensure` that finds the workspace image missing fetches it
        // inside the request, and this is what it is allowed to take. A
        // resident guest keeps the short budget: it hands back a retryable
        // answer in seconds and fetches on a worker thread, and a longer
        // budget there would only be a longer wait on a wedged guest.
        //
        // Larger than the guest's own `ENGINE_PULL_TIMEOUT`, in the same
        // direction and for the same reason as the images above: if this
        // expired first the guest would keep fetching into a request nobody is
        // waiting on, and the next attempt would be told the image is busy by
        // a download whose result no longer has anywhere to go.
        "sandbox.ensure" if transport == GuestTransport::PerRequest => Duration::from_secs(75 * 60),
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
        let budget = guest_request_budget(operation, GuestTransport::of_this_host());
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
