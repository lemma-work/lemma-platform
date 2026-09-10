//! The container engine, and running one of its commands under a bound.

use super::*;

pub trait Engine: Send + Sync {
    fn run(&self, arguments: &[String]) -> Result<Output, String>;
}

pub struct NerdctlEngine {
    pub(crate) executable: PathBuf,
    pub(crate) capture_root: PathBuf,
}

impl NerdctlEngine {
    pub fn discover(state_root: &Path) -> Result<Self, GuestError> {
        let configured = std::env::var_os("LEMMA_NERDCTL_BIN")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/usr/local/bin/nerdctl"));
        if !configured.is_file() {
            return Err(GuestError::engine(format!(
                "managed container engine is missing: {}",
                configured.display()
            )));
        }
        let capture_root = std::env::var_os("LEMMA_GUEST_TEMP_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| state_root.join("run/engine-tmp"));
        fs::create_dir_all(&capture_root).map_err(|error| {
            GuestError::engine(format!(
                "could not prepare writable container-engine temporary storage at {}: {error}",
                capture_root.display()
            ))
        })?;
        fs::set_permissions(&capture_root, fs::Permissions::from_mode(0o700)).map_err(|error| {
            GuestError::engine(format!(
                "could not secure container-engine temporary storage at {}: {error}",
                capture_root.display()
            ))
        })?;
        Ok(Self {
            executable: configured,
            capture_root,
        })
    }
}

impl Engine for NerdctlEngine {
    fn run(&self, arguments: &[String]) -> Result<Output, String> {
        let timeout = if arguments.first().is_some_and(|value| value == "pull") {
            ENGINE_PULL_TIMEOUT
        } else {
            ENGINE_COMMAND_TIMEOUT
        };
        run_bounded_engine_command(&self.executable, &self.capture_root, arguments, timeout)
    }
}

pub(crate) fn run_bounded_engine_command(
    executable: &Path,
    capture_root: &Path,
    arguments: &[String],
    timeout: Duration,
) -> Result<Output, String> {
    // File-backed capture avoids the classic timeout deadlock where a forked
    // helper keeps a stdout/stderr pipe open after its parent is terminated.
    // Keep both our captures and nerdctl's inherited TMPDIR on the app-owned
    // writable data disk. The appliance root, including /root, is immutable.
    let mut stdout = tempfile::tempfile_in(capture_root).map_err(|error| {
        format!(
            "could not create container-engine stdout capture in {}: {error}",
            capture_root.display()
        )
    })?;
    let mut stderr = tempfile::tempfile_in(capture_root).map_err(|error| {
        format!(
            "could not create container-engine stderr capture in {}: {error}",
            capture_root.display()
        )
    })?;
    let mut command = Command::new(executable);
    command
        .args(["--namespace", "lemma"])
        .args(arguments)
        .env("TMPDIR", capture_root)
        .stdin(Stdio::null())
        .stdout(Stdio::from(
            stdout.try_clone().map_err(|error| error.to_string())?,
        ))
        .stderr(Stdio::from(
            stderr.try_clone().map_err(|error| error.to_string())?,
        ))
        .process_group(0);
    // A just-written, just-chmod'd executable can make exec() answer ETXTBSY
    // ("text file busy") even though this process already closed its own
    // write handle -- the underlying storage layer's busy state can lag the
    // close() that cleared it, especially under concurrent I/O (observed in
    // CI on the runner's overlayfs). Spurious and short-lived: retry a
    // handful of times on that one specific error rather than surface a
    // transient race as a real spawn failure. Any other spawn error still
    // fails immediately, unchanged.
    let mut spawn_attempts = 0;
    let mut child = loop {
        match command.spawn() {
            Ok(child) => break child,
            Err(error) if error.raw_os_error() == Some(libc::ETXTBSY) && spawn_attempts < 5 => {
                spawn_attempts += 1;
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(error.to_string()),
        }
    };
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            break status;
        }
        if Instant::now() >= deadline {
            let process_group = -(child.id() as i32);
            // The child is its own process-group leader. Killing the group
            // bounds nerdctl helpers as well as the top-level client.
            unsafe {
                libc::kill(process_group, libc::SIGKILL);
            }
            let _ = child.wait();
            // Naming the verb, because "engine command" is true of pulling a
            // gigabyte of images and of listing containers, and those are not
            // the same problem to the person reading it.
            let verb = arguments.first().map_or("command", String::as_str);
            return Err(format!(
                "managed container engine `{verb}` timed out after {}s. \
                 If this was a first install, it was downloading images, and \
                 the download is bounded by this computer's connection.",
                timeout.as_secs()
            ));
        }
        thread::sleep(Duration::from_millis(100));
    };

    let mut stdout_bytes = Vec::new();
    let mut stderr_bytes = Vec::new();
    stdout
        .seek(SeekFrom::Start(0))
        .and_then(|_| stdout.read_to_end(&mut stdout_bytes))
        .map_err(|error| error.to_string())?;
    stderr
        .seek(SeekFrom::Start(0))
        .and_then(|_| stderr.read_to_end(&mut stderr_bytes))
        .map_err(|error| error.to_string())?;
    Ok(Output {
        status,
        stdout: stdout_bytes,
        stderr: stderr_bytes,
    })
}

pub(crate) fn redact_engine_error(value: &str) -> String {
    let lines = value
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();
    // Container CLIs often emit one or more warnings before the actionable
    // fatal diagnostic. Returning the first line made a preserved-volume
    // warning look like the reason a later container creation failed.
    let line = lines
        .iter()
        .rev()
        .copied()
        .find(|line| line.contains("level=fatal") || line.contains("level=error"))
        .or_else(|| lines.last().copied())
        .unwrap_or("container engine failed");
    if line.len() > 512 {
        format!("{}…", &line[..512])
    } else {
        line.into()
    }
}

impl<E: Engine + 'static> GuestService<E> {
    pub(crate) fn inspect_raw(&self, name: &str) -> Result<Option<Value>, GuestError> {
        let output = self
            .engine
            .run(&["inspect".into(), name.into()])
            .map_err(GuestError::engine)?;
        if !output.status.success() {
            return Ok(None);
        }
        let parsed: Value = serde_json::from_slice(&output.stdout)
            .map_err(|error| GuestError::engine(format!("invalid inspect response: {error}")))?;
        Ok(parsed.as_array().and_then(|items| items.first()).cloned())
    }

    /// The engine's id for a container named `name`, if it has one.
    ///
    /// Asked by name because the stop path has to tell the data services apart
    /// from everything else, and `ps --quiet` prints ids alone.
    pub(crate) fn container_id(&self, name: &str) -> Result<Option<String>, GuestError> {
        let Some(details) = self.inspect_raw(name)? else {
            return Ok(None);
        };
        Ok(details
            .get("Id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
            .map(str::to_owned))
    }

    /// Why a core container is not answering, in its own words.
    ///
    /// This used to return the single last non-empty line, which is fine for a
    /// crash that ends in one and useless for the case that matters most.
    /// PostgreSQL refuses an unusable data directory with a *paragraph*, and
    /// the last line of the official image's version-mismatch block is
    ///
    /// ```text
    /// discussion around this process, and suggestions for how to do so.
    /// ```
    ///
    /// which is what a user was shown, appended to a nerdctl error, as the
    /// whole explanation for an install that would never finish. The sentence
    /// that says what is wrong is several lines above it.
    ///
    /// So: keep the tail, drop the noise, and cap the length. A few lines of
    /// the container's own output is the difference between "something stopped"
    /// and "your data was made by a different PostgreSQL".
    pub(crate) fn container_log_summary(&self, name: &str) -> Option<String> {
        const KEEP_LINES: usize = 6;
        const MAX_CHARS: usize = 600;
        let output = self
            .engine
            .run(&["logs".into(), "--tail".into(), "40".into(), name.into()])
            .ok()?;
        // Both streams: the refusal goes to stderr, but an image that logs
        // its reason to stdout and exits quietly would otherwise report
        // nothing at all.
        let mut logs = String::from_utf8_lossy(&output.stdout).into_owned();
        let stderr = String::from_utf8_lossy(&output.stderr);
        if !stderr.trim().is_empty() {
            if !logs.trim().is_empty() {
                logs.push('\n');
            }
            logs.push_str(&stderr);
        }
        let lines = logs
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            // Decorative rules are what the image wraps its refusal in, and
            // they crowd out the sentence underneath at this length.
            .filter(|line| !line.chars().all(|c| c == '*' || c == '-' || c == '='))
            .collect::<Vec<_>>();
        if lines.is_empty() {
            return None;
        }
        let summary = lines[lines.len().saturating_sub(KEEP_LINES)..].join(" ");
        Some(if summary.chars().count() > MAX_CHARS {
            let cut: String = summary.chars().take(MAX_CHARS).collect();
            format!("{cut}…")
        } else {
            summary
        })
    }

    pub(crate) fn wait_engine_command(
        &self,
        arguments: &[String],
        timeout: u64,
    ) -> Result<(), GuestError> {
        self.wait_engine_output(arguments, timeout).map(|_| ())
    }

    pub(crate) fn wait_engine_output(
        &self,
        arguments: &[String],
        timeout: u64,
    ) -> Result<String, GuestError> {
        let deadline = Instant::now() + Duration::from_secs(timeout);
        let mut last_error = None;
        while Instant::now() < deadline {
            match self.engine.run(arguments) {
                Ok(output) if output.status.success() => {
                    return String::from_utf8(output.stdout)
                        .map(|value| value.trim().to_owned())
                        .map_err(|_| {
                            GuestError::engine("container engine returned non-UTF8 output")
                        })
                }
                Ok(output) => {
                    last_error = Some(redact_engine_error(&String::from_utf8_lossy(
                        &output.stderr,
                    )))
                }
                Err(error) => last_error = Some(error),
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(GuestError::engine(last_error.unwrap_or_else(|| {
            "core service readiness timed out".into()
        })))
    }

    pub(crate) fn run_checked(&self, arguments: &[String]) -> Result<String, GuestError> {
        let output = self.engine.run(arguments).map_err(GuestError::engine)?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(GuestError::engine(redact_engine_error(&stderr)));
        }
        String::from_utf8(output.stdout)
            .map(|value| value.trim().to_owned())
            .map_err(|_| GuestError::engine("container engine returned non-UTF8 output"))
    }
}
