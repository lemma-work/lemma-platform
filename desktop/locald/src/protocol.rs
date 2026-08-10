use std::fs::OpenOptions;
use std::io::{self, BufRead, Read, Write};
use std::path::Path;

use serde_json::{json, Value};

use crate::PROTOCOL_VERSION;

pub const MAX_MESSAGE_BYTES: usize = 1024 * 1024;

pub fn load_or_create_token(path: &Path) -> io::Result<String> {
    if path.exists() {
        let token = std::fs::read_to_string(path)?;
        let token = token.trim();
        if token.len() == 64 && token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Ok(token.to_owned());
        }
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "locald control token is malformed",
        ));
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
    fn token_is_private_stable_and_well_formed() {
        let root = tempdir().unwrap();
        let path = root.path().join("token");
        let first = load_or_create_token(&path).unwrap();
        let second = load_or_create_token(&path).unwrap();
        assert_eq!(first, second);
        assert_eq!(first.len(), 64);

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
