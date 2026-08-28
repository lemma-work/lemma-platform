use std::fs::OpenOptions;
use std::io::{self, BufRead, Read, Write};
use std::path::Path;

use serde_json::{json, Value};

use crate::PROTOCOL_VERSION;

pub const MAX_MESSAGE_BYTES: usize = 1024 * 1024;

/// Read the control token, replacing it if it is unusable.
///
/// A truncated or unreadable token used to be fatal, which meant a single bad
/// write left an installation permanently unable to start -- and, because the
/// reason went to a null stderr, unable to say why. Nothing depends on the
/// token's *value*: every client re-reads it from disk on each connect. So the
/// only cost of reminting is that an already-connected client is invalidated,
/// which is the right outcome for a token nobody could have been holding.
///
/// The old file is moved aside rather than deleted, so a support request can
/// still ask what was there. Heals are appended to `healed` for the caller to
/// log and broadcast -- silently replacing a credential would be worse than
/// failing.
pub fn load_or_create_token(path: &Path, healed: &mut Vec<String>) -> io::Result<String> {
    if path.exists() {
        // `read_to_string` covers the unreadable case too -- bad permissions, a
        // directory where a file belongs. That used to fall through to the mint
        // below, which opens with `create_new` and so failed a second time.
        match std::fs::read_to_string(path) {
            Ok(token) => {
                let token = token.trim();
                if token.len() == 64 && token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                    return Ok(token.to_owned());
                }
                let aside = crate::paths::quarantine_aside(path)?;
                healed.push(format!(
                    "the control token was malformed; kept as {} and replaced",
                    aside.display()
                ));
            }
            Err(error) => {
                let aside = crate::paths::quarantine_aside(path)?;
                healed.push(format!(
                    "the control token could not be read ({error}); kept as {} and replaced",
                    aside.display()
                ));
            }
        }
    }

    let mut random = [0_u8; 32];
    getrandom::fill(&mut random)
        .map_err(|error| io::Error::other(format!("could not generate token: {error}")))?;
    let token = hex(&random);
    write_private_new(path, format!("{token}\n").as_bytes())?;
    Ok(token)
}

fn write_private_new(path: &Path, contents: &[u8]) -> io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(contents)?;
    file.sync_all()
}

fn hex(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(ALPHABET[(byte >> 4) as usize] as char);
        encoded.push(ALPHABET[(byte & 0x0f) as usize] as char);
    }
    encoded
}

pub fn authenticate(message: &Value, expected_token: &str) -> bool {
    message.get("v").and_then(Value::as_u64) == Some(PROTOCOL_VERSION)
        && message.get("cmd").and_then(Value::as_str) == Some("hello")
        && message.get("token").and_then(Value::as_str) == Some(expected_token)
}

pub fn error_event(code: &str, message: impl Into<String>, id: Option<&Value>) -> Value {
    let mut event = json!({
        "v": PROTOCOL_VERSION,
        "event": "error",
        "code": code,
        "message": message.into(),
    });
    if let Some(id) = id {
        event["id"] = id.clone();
    }
    event
}

pub fn read_bounded_line<R: Read>(reader: &mut io::BufReader<R>) -> io::Result<Option<String>> {
    let mut bytes = Vec::with_capacity(256);
    let count = reader
        .by_ref()
        .take((MAX_MESSAGE_BYTES + 1) as u64)
        .read_until(b'\n', &mut bytes)?;
    if count == 0 {
        return Ok(None);
    }
    if bytes.len() > MAX_MESSAGE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "locald protocol message exceeds 1 MiB",
        ));
    }
    if bytes.last() == Some(&b'\n') {
        bytes.pop();
    }
    if bytes.last() == Some(&b'\r') {
        bytes.pop();
    }
    String::from_utf8(bytes)
        .map(Some)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "message is not UTF-8"))
}

/// Append one line to `locald.log`, rotating it at 5 MiB.
///
/// A free function rather than a `Daemon` method because the failures worth
/// recording most happen *inside* `Daemon::new` -- a malformed control token, an
/// unparseable operator config, a port that could not be reserved. Those used to
/// reach only `eprintln!`, and the shell spawns this daemon with a null stderr,
/// so the single most likely class of first-run failure left no record anywhere.
///
/// Creates the parent directory: `paths.ensure()` is the first thing
/// `Daemon::new` does and it can itself be what failed.
pub fn append_bounded_daemon_log(path: &Path, line: &str) -> io::Result<()> {
    const MAX_DAEMON_LOG_BYTES: u64 = 5 * 1024 * 1024;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= MAX_DAEMON_LOG_BYTES)
    {
        let previous = path.with_extension("previous.log");
        let _ = std::fs::remove_file(&previous);
        std::fs::rename(path, previous)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{line}")
}

pub fn append_bounded_journal(path: &Path, line: &str) -> io::Result<()> {
    const MAX_JOURNAL_BYTES: u64 = 5 * 1024 * 1024;
    if path.metadata().map(|meta| meta.len()).unwrap_or(0) >= MAX_JOURNAL_BYTES {
        let previous = path.with_extension("jsonl.previous");
        let _ = std::fs::remove_file(&previous);
        std::fs::rename(path, previous)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{line}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn a_malformed_token_is_replaced_rather_than_ending_the_daemon() {
        let root = tempdir().unwrap();
        let path = root.path().join("control.token");
        std::fs::write(&path, "deadbeef").unwrap();

        let mut healed = Vec::new();
        let token = load_or_create_token(&path, &mut healed).unwrap();

        assert_eq!(token.len(), 64);
        assert_eq!(healed.len(), 1, "the replacement is reported, not silent");
        assert!(healed[0].contains("malformed"), "{}", healed[0]);
        // The original bytes survive next door for whoever has to explain this.
        let aside: Vec<_> = std::fs::read_dir(root.path())
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".invalid-"))
            .collect();
        assert_eq!(aside.len(), 1);
        assert_eq!(
            std::fs::read_to_string(aside[0].path()).unwrap(),
            "deadbeef"
        );
    }

    #[test]
    fn a_healed_token_is_usable_on_the_very_next_start() {
        let root = tempdir().unwrap();
        let path = root.path().join("control.token");
        std::fs::write(&path, "not a token").unwrap();

        let mut healed = Vec::new();
        let first = load_or_create_token(&path, &mut healed).unwrap();
        // The second call must find a valid file and report nothing -- a heal
        // that did not actually persist would loop forever.
        let mut second_pass = Vec::new();
        let second = load_or_create_token(&path, &mut second_pass).unwrap();

        assert_eq!(first, second);
        assert!(second_pass.is_empty());
    }

    #[test]
    fn token_is_private_stable_and_well_formed() {
        let root = tempdir().unwrap();
        let path = root.path().join("token");
        let mut healed = Vec::new();
        let first = load_or_create_token(&path, &mut healed).unwrap();
        let second = load_or_create_token(&path, &mut healed).unwrap();
        assert_eq!(first, second);
        assert_eq!(first.len(), 64);
        assert!(healed.is_empty(), "a healthy token heals nothing");

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(path.metadata().unwrap().permissions().mode() & 0o777, 0o600);
        }
    }

    #[test]
    fn authentication_requires_version_command_and_capability() {
        assert!(authenticate(
            &json!({"v": 1, "cmd": "hello", "token": "secret"}),
            "secret"
        ));
        assert!(!authenticate(
            &json!({"v": 1, "cmd": "hello", "token": "wrong"}),
            "secret"
        ));
        assert!(!authenticate(
            &json!({"v": 2, "cmd": "hello", "token": "secret"}),
            "secret"
        ));
    }

    #[test]
    fn oversized_lines_fail_closed() {
        let bytes = vec![b'x'; MAX_MESSAGE_BYTES + 1];
        let mut reader = io::BufReader::new(bytes.as_slice());
        assert_eq!(
            read_bounded_line(&mut reader).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
    }
}
