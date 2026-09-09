use super::*;

pub(crate) fn diagnostic_log_sources() -> Vec<(&'static str, &'static str, PathBuf)> {
    let root = locald_root();
    #[cfg(target_os = "macos")]
    let vm_log = root.join("logs/vz.log");
    #[cfg(windows)]
    let vm_log = root.join("logs/wsl.log");
    #[cfg(all(unix, not(target_os = "macos")))]
    let vm_log = root.join("logs/runtime.log");
    #[cfg(target_os = "macos")]
    let guest_log = root.join("runtime/macos/console.log");
    #[cfg(not(target_os = "macos"))]
    let guest_log = root.join("logs/guest.log");
    vec![
        ("events", "Events", root.join("events.jsonl")),
        ("migrations", "Migrations", root.join("logs/migrations.log")),
        ("backend", "Backend", root.join("logs/backend.log")),
        ("frontend", "Frontend", root.join("logs/frontend.log")),
        ("vm", "VM helper", vm_log),
        ("guest", "Guest services", guest_log),
        ("locald", "Service manager", root.join("locald.log")),
        // Separate from "locald" on purpose: this is what the daemon said on
        // its way out, which is the one thing locald.log cannot contain when
        // the failure was constructing the daemon in the first place.
        (
            "locald-stderr",
            "Service manager startup",
            locald_stderr_path(),
        ),
        (
            "agent-host",
            "Agent Host",
            root.parent()
                .unwrap_or(root.as_path())
                .join("agent-host/agent-host.log"),
        ),
        ("installer", "Installer", install_log_path()),
        ("launch", "Launch timing", launch_log_path()),
    ]
}

pub(crate) fn parse_diagnostic_cursor(cursor: &str) -> Option<(String, u64)> {
    let value = cursor.strip_prefix("v1:")?;
    let (identity, offset) = value.rsplit_once(':')?;
    Some((identity.to_owned(), offset.parse().ok()?))
}

#[cfg(unix)]
pub(crate) fn diagnostic_file_identity(file: &std::fs::File) -> String {
    use std::os::unix::fs::MetadataExt;
    match file.metadata() {
        Ok(metadata) => format!("{:x}-{:x}", metadata.dev(), metadata.ino()),
        Err(_) => String::new(),
    }
}

#[cfg(windows)]
pub(crate) fn diagnostic_file_identity(file: &std::fs::File) -> String {
    // (volume serial, file index) is the Windows spelling of (device, inode).
    // `Metadata` only exposes it behind the unstable `windows_by_handle`
    // feature, but the same fields are on the stable Win32 call, and we are
    // holding the open handle it wants.
    //
    // Anything derived from the file's *contents* -- size, last write -- is
    // wrong here even though it compiles: an identity that changes whenever
    // the log is appended to invalidates the cursor on every poll, which
    // re-sends the whole tail exactly while the user is watching a live log.
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };

    let mut information = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    // SAFETY: the handle is live for as long as `file` is borrowed, and
    // `information` is a correctly sized, writable destination.
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as _, &mut information) } == 0 {
        // An empty identity never matches a cursor, so the caller falls back
        // to re-reading the tail -- the same behaviour as a rotated file.
        return String::new();
    }
    let index =
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow);
    format!("{:x}-{:x}", information.dwVolumeSerialNumber, index)
}

#[cfg(not(any(unix, windows)))]
pub(crate) fn diagnostic_file_identity(_file: &std::fs::File) -> String {
    // No portable file identity, so every poll re-reads the tail.
    String::new()
}

pub(crate) fn redact_diagnostic_text(mut text: String) -> String {
    let root = locald_root();
    let mut secrets = Vec::new();
    for path in [
        root.join("control.token"),
        root.join("host.secrets.json"),
        root.join("infra.secrets.json"),
        root.join("operator-config.json"),
    ] {
        collect_secret_file_values(&path, &mut secrets);
    }
    secrets.sort_by_key(|value| std::cmp::Reverse(value.len()));
    secrets.dedup();
    for secret in secrets {
        text = text.replace(&secret, "[redacted]");
    }
    mask_secret_shapes(text)
}

/// Mask credentials by what they look like, not by having seen them before.
///
/// Substitution alone cannot cover the ones that matter. All 19 operator
/// secrets -- the AI provider key, Slack and Telegram tokens, OAuth client
/// secrets, the encryption keyset -- live in the OS credential vault, and
/// `operator-config.json` holds none of them. So the values were unredactable
/// by construction, while the README told the user this view returned bounded,
/// redacted data.
///
/// Reading them back out of the vault to redact them would put every secret
/// this installation owns into a diagnostics buffer and possibly raise a
/// system authorisation prompt, so this recognises their shapes instead.
/// Deliberately conservative: a missed token is worse than a masked path, but a
/// log so heavily masked that nobody can read it is not a diagnostic.
pub(crate) fn mask_secret_shapes(text: String) -> String {
    text.lines()
        .map(|line| {
            let lowered = line.to_ascii_lowercase();
            // An Authorization header carries a credential in full, whatever
            // scheme it names.
            if let Some(index) = lowered.find("authorization:") {
                let (head, _) = line.split_at(index + "authorization:".len());
                return format!("{head} [redacted]");
            }
            // Rebuilt around the words rather than from them.
            //
            // This used to be `split_whitespace().join(" ")`, which redacts
            // correctly and flattens the line on the way past: every indent,
            // every tab, every aligned column gone. Diagnostics is where
            // somebody reads a Python traceback, and a traceback with no
            // indentation is a wall of text. The guard test could not see it --
            // its fixtures were all single-spaced.
            let mut masked = String::with_capacity(line.len());
            let mut rest = line;
            while !rest.is_empty() {
                let gap = rest
                    .find(|c: char| !c.is_whitespace())
                    .unwrap_or(rest.len());
                masked.push_str(&rest[..gap]);
                rest = &rest[gap..];
                if rest.is_empty() {
                    break;
                }
                let end = rest.find(char::is_whitespace).unwrap_or(rest.len());
                let word = &rest[..end];
                let trimmed = word.trim_matches(|c: char| {
                    c == '"' || c == '\'' || c == ',' || c == ';' || c == ')'
                });
                if looks_like_a_credential(trimmed) {
                    masked.push_str(&word.replace(trimmed, "[redacted]"));
                } else {
                    masked.push_str(word);
                }
                rest = &rest[end..];
            }
            masked
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Whether one whitespace-delimited word is a credential rather than prose.
///
/// Prefix-anchored on the vendor forms actually stored here, plus JWTs. Length
/// alone is not enough -- a file path or a container digest would match, and
/// masking those makes a log useless for the thing it is being read for.
pub(crate) fn looks_like_a_credential(word: &str) -> bool {
    const VENDOR_PREFIXES: [&str; 7] = ["sk-", "xoxb-", "xoxp-", "xapp-", "re_", "ghp_", "ghs_"];
    if VENDOR_PREFIXES
        .iter()
        .any(|prefix| word.len() > prefix.len() + 12 && word.starts_with(prefix))
    {
        return true;
    }
    // A JWT: three dot-separated base64url segments, the first of which decodes
    // to a JSON header. Checking the shape rather than the length keeps
    // version strings and digests out of it.
    let segments: Vec<&str> = word.split('.').collect();
    segments.len() == 3
        && segments[0].len() >= 8
        && segments.iter().all(|segment| {
            !segment.is_empty()
                && segment
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
        })
        && segments[0].starts_with("eyJ")
}

pub(crate) fn collect_secret_file_values(path: &Path, output: &mut Vec<String>) {
    let Ok(raw) = std::fs::read_to_string(path) else {
        return;
    };
    if path.extension().and_then(|value| value.to_str()) != Some("json") {
        let value = raw.trim();
        if value.len() >= 8 {
            output.push(value.into());
        }
        return;
    }
    let Ok(value) = serde_json::from_str::<Value>(&raw) else {
        return;
    };
    collect_secret_json_values(&value, false, output);
}

pub(crate) fn collect_secret_json_values(value: &Value, sensitive: bool, output: &mut Vec<String>) {
    match value {
        Value::Object(values) => {
            for (key, value) in values {
                let key = key.to_ascii_lowercase();
                let child_sensitive = sensitive
                    || ["password", "secret", "token", "api_key", "apikey"]
                        .iter()
                        .any(|marker| key.contains(marker));
                collect_secret_json_values(value, child_sensitive, output);
            }
        }
        Value::Array(values) => {
            for value in values {
                collect_secret_json_values(value, sensitive, output);
            }
        }
        Value::String(value) if sensitive && value.len() >= 8 => output.push(value.clone()),
        _ => {}
    }
}

#[tauri::command]
pub(crate) fn open_developer_tools(window: Webview, app: AppHandle) -> Result<(), String> {
    require_control_window(&window)?;
    let main = app
        .get_webview("main")
        .ok_or("main window is not available")?;
    main.open_devtools();
    let _ = main.window().show();
    let _ = main.set_focus();
    Ok(())
}

#[tauri::command(async)]
pub(crate) fn installer_log(window: Webview) -> Result<String, String> {
    require_local_native_window(&window)?;
    let path = install_log_path();
    let raw = match std::fs::read_to_string(&path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok("No local installer log entries yet.".into());
        }
        Err(error) => {
            return Err(format!(
                "could not read local installer log {}: {error}",
                path.display()
            ));
        }
    };
    let mut lines: Vec<&str> = raw.lines().rev().take(500).collect();
    lines.reverse();
    Ok(lines.join("\n"))
}

#[tauri::command(async)]
pub(crate) fn diagnostic_logs(
    window: Webview,
    source: Option<String>,
    cursor: Option<String>,
) -> Result<DiagnosticLogSnapshot, String> {
    require_local_native_window(&window)?;
    let sources = diagnostic_log_sources();
    let selected = source.as_deref().unwrap_or("events");
    let (_, _, path) = sources
        .iter()
        .find(|(id, _, _)| *id == selected)
        .ok_or_else(|| format!("unknown diagnostic log source: {selected}"))?;
    let public_sources = sources
        .iter()
        .map(|(id, label, _)| DiagnosticLogSource {
            id: (*id).into(),
            label: (*label).into(),
        })
        .collect();

    let mut file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(DiagnosticLogSnapshot {
                sources: public_sources,
                source: selected.into(),
                entries: format!("No {selected} log entries yet."),
                next_cursor: String::new(),
            });
        }
        Err(error) => {
            return Err(format!(
                "could not read diagnostic log {}: {error}",
                path.display()
            ));
        }
    };
    let metadata = file.metadata().map_err(|error| error.to_string())?;
    let length = metadata.len();
    let identity = diagnostic_file_identity(&file);
    let start = cursor
        .as_deref()
        .and_then(parse_diagnostic_cursor)
        .filter(|(cursor_identity, offset)| cursor_identity == &identity && *offset <= length)
        .map(|(_, offset)| offset)
        .unwrap_or_else(|| length.saturating_sub(MAX_DIAGNOSTIC_LOG_READ));
    file.seek(SeekFrom::Start(start))
        .map_err(|error| error.to_string())?;
    let mut bytes = Vec::new();
    file.take(MAX_DIAGNOSTIC_LOG_READ)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    let next_cursor = format!("v1:{identity}:{}", start.saturating_add(bytes.len() as u64));
    let mut entries = String::from_utf8_lossy(&bytes).into_owned();
    if start > 0 {
        if let Some(newline) = entries.find('\n') {
            entries.drain(..=newline);
        }
    }
    entries = redact_diagnostic_text(entries);
    Ok(DiagnosticLogSnapshot {
        sources: public_sources,
        source: selected.into(),
        entries,
        next_cursor,
    })
}
