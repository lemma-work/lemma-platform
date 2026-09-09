//! Running a provider's binary, and reclaiming one this installation
//! left behind.

use super::*;

pub(crate) fn find_executable(name: &str) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = std::env::var_os("PATH") {
        for root in std::env::split_paths(&path) {
            candidates.push(root.join(executable_name(name)));
        }
    }
    #[cfg(target_os = "macos")]
    {
        candidates.push(PathBuf::from("/opt/homebrew/bin").join(name));
        candidates.push(PathBuf::from("/usr/local/bin").join(name));
    }
    candidates.into_iter().find(|candidate| candidate.is_file())
}

pub(crate) fn record_owned_tunnel(
    root: &Path,
    marker_path: &Path,
    provider: TunnelProvider,
    executable: &Path,
    child: &Child,
) -> io::Result<()> {
    let expected = executable.canonicalize()?;
    let identity = process_identity(child.id())?;
    if Path::new(&identity.executable).canonicalize()? != expected {
        return Err(io::Error::other(
            "the started tunnel executable identity did not match the selected CLI",
        ));
    }
    persist_private_json(
        marker_path,
        &TunnelProcessMarker {
            schema_version: PROCESS_MARKER_SCHEMA_VERSION,
            installation_id: installation_identity(root)?,
            provider,
            pid: child.id(),
            executable: identity.executable,
            start_identity: identity.start_identity,
        },
    )
}

pub(crate) fn reclaim_owned_tunnel(root: &Path) -> io::Result<()> {
    let marker_path = root.join("sharing-process.json");
    let raw = match fs::read(&marker_path) {
        Ok(raw) if raw.len() <= 64 * 1024 => raw,
        Ok(_) => return Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let Ok(marker) = serde_json::from_slice::<TunnelProcessMarker>(&raw) else {
        return Ok(());
    };
    if marker.schema_version != PROCESS_MARKER_SCHEMA_VERSION {
        return Ok(());
    }
    let current_installation = installation_identity(root)?;
    let identity = match process_identity(marker.pid) {
        Ok(identity) => identity,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let _ = fs::remove_file(&marker_path);
            return Ok(());
        }
        Err(error) => return Err(error),
    };
    if marker.installation_id != current_installation {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "a sharing process marker belongs to another Lemma installation",
        ));
    }
    let expected_name = provider_name(marker.provider);
    let expected = find_executable(expected_name).and_then(|path| path.canonicalize().ok());
    let exact = expected.is_some_and(|expected| {
        Path::new(&identity.executable)
            .canonicalize()
            .is_ok_and(|actual| actual == expected)
    }) && identity.executable == marker.executable
        && identity.start_identity == marker.start_identity;
    if exact {
        terminate_verified_process(marker.pid)?;
    }
    let _ = fs::remove_file(&marker_path);
    Ok(())
}

pub(crate) fn executable_name(name: &str) -> String {
    #[cfg(windows)]
    {
        format!("{name}.exe")
    }
    #[cfg(not(windows))]
    {
        name.to_owned()
    }
}

pub(crate) fn command_text(executable: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new(executable)
        .no_console_window()
        .args(args)
        .output()
        .map_err(|error| error.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{}\n{}", stdout.trim(), stderr.trim())
        .trim()
        .to_owned();
    if output.status.success() {
        Ok(combined)
    } else {
        Err(combined)
    }
}

pub(crate) fn checked_command_output(command: &mut Command, context: &str) -> io::Result<Vec<u8>> {
    let output = command
        .output()
        .map_err(|error| io::Error::other(format!("{context}: {error}")))?;
    const MAX_OUTPUT: usize = 1024 * 1024;
    if output.stdout.len() > MAX_OUTPUT || output.stderr.len() > MAX_OUTPUT {
        return Err(io::Error::other(format!(
            "{context}: cloudflared returned unexpectedly large output"
        )));
    }
    if output.status.success() {
        return Ok(output.stdout);
    }
    let detail = format!(
        "{} {}",
        String::from_utf8_lossy(&output.stderr),
        String::from_utf8_lossy(&output.stdout)
    );
    Err(io::Error::other(format!(
        "{context}: {}",
        redact_error(detail.trim())
    )))
}

pub(crate) fn redact_error(value: &str) -> String {
    value
        .split_whitespace()
        .map(|part| {
            if part.to_ascii_lowercase().contains("token") && part.len() > 18 {
                "[redacted]"
            } else {
                part
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

pub(crate) fn first_line(value: String) -> String {
    value.lines().next().unwrap_or_default().trim().to_owned()
}

pub(crate) fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(unix)]
pub(crate) fn prepare_owned_command(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(windows)]
pub(crate) fn prepare_owned_command(_command: &mut Command) {}

#[cfg(unix)]
pub(crate) fn terminate_owned_child(child: &mut Child) {
    let pid = child.id() as i32;
    unsafe {
        libc::kill(-pid, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    let _ = child.wait();
}

#[cfg(windows)]
pub(crate) fn terminate_owned_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}
