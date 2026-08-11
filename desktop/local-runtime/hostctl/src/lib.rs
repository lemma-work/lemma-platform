use serde_json::Value;
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::process::{Command, Stdio};

const MAX_REQUEST_BYTES: u64 = 1024 * 1024;
const MAX_RESPONSE_BYTES: u64 = 4 * 1024 * 1024;
// Windows reaches the guest by running `wsl.exe --exec`, and waits on the child
// rather than on a socket, so only the unix transport has a read to bound.
#[cfg(unix)]
const RESPONSE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(8 * 60);

pub enum Transport {
    #[cfg(unix)]
    Unix(PathBuf),
    #[cfg(windows)]
    Wsl {
        executable: PathBuf,
        distribution: String,
    },
}

/// Spawn a child without flashing up a console window.
///
/// wsl.exe is a console program and this bridge runs under locald, which the
/// GUI app starts without a console. Without this flag every guest command
/// would open a console window of its own.
///
/// Windows-only here: the bridge only shells out to wsl.exe, and that call
/// site does not exist on other platforms.
#[cfg(windows)]
trait NoConsoleWindow {
    fn no_console_window(&mut self) -> &mut Self;
}

#[cfg(windows)]
impl NoConsoleWindow for std::process::Command {
    fn no_console_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        self.creation_flags(CREATE_NO_WINDOW)
    }
}

pub struct BridgeConfig {
    pub capability_file: PathBuf,
    pub transport: Transport,
}

impl BridgeConfig {
    pub fn discover() -> io::Result<Self> {
        let capability_file = std::env::var_os("LEMMA_GUEST_CAPABILITY_FILE")
            .map(PathBuf::from)
            .unwrap_or_else(|| local_root().join("guest.capability"));
        #[cfg(unix)]
        let transport = Transport::Unix(
            std::env::var_os("LEMMA_GUEST_CONTROL_SOCKET")
                .map(PathBuf::from)
                .unwrap_or_else(|| local_root().join("guest.sock")),
        );
        #[cfg(windows)]
        let transport = Transport::Wsl {
            executable: std::env::var_os("LEMMA_WSL_BIN")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("wsl.exe")),
            distribution: std::env::var("LEMMA_WSL_DISTRIBUTION")
                .unwrap_or_else(|_| "LemmaRuntime".into()),
        };
        Ok(Self {
            capability_file,
            transport,
        })
    }
}

pub fn request<R: Read, W: Write>(
    reader: R,
    mut writer: W,
    config: &BridgeConfig,
) -> io::Result<bool> {
    let mut bounded = BufReader::new(reader).take(MAX_REQUEST_BYTES + 1);
    let mut raw = String::new();
    bounded.read_line(&mut raw)?;
    if raw.len() as u64 > MAX_REQUEST_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "runtime request exceeded 1 MiB",
        ));
    }
    let mut payload: Value = serde_json::from_str(raw.trim_end()).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("invalid runtime request: {error}"),
        )
    })?;
    let object = payload.as_object_mut().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "runtime request must be an object",
        )
    })?;
    if object.contains_key("capability") {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "caller cannot supply the guest capability",
        ));
    }
    object.insert(
        "capability".into(),
        Value::String(read_capability(&config.capability_file)?),
    );
    let encoded = serde_json::to_vec(&payload)?;
    if encoded.len() as u64 > MAX_REQUEST_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "authenticated runtime request exceeded 1 MiB",
        ));
    }
    let response = exchange(&config.transport, &encoded)?;
    if response.len() as u64 > MAX_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "runtime response exceeded 4 MiB",
        ));
    }
    let parsed: Value = serde_json::from_slice(&response).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("guest returned invalid JSON: {error}"),
        )
    })?;
    let ok = parsed
        .get("ok")
        .and_then(Value::as_bool)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "guest response omitted ok"))?;
    writer.write_all(&response)?;
    if !response.ends_with(b"\n") {
        writer.write_all(b"\n")?;
    }
    writer.flush()?;
    Ok(ok)
}

fn read_capability(path: &Path) -> io::Result<String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "guest capability must be a private regular file",
            ));
        }
    }
    let value = fs::read_to_string(path)?;
    let value = value.trim();
    if value.len() < 32 || value.len() > 512 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "guest capability has invalid length",
        ));
    }
    Ok(value.into())
}

#[cfg(unix)]
fn exchange(transport: &Transport, request: &[u8]) -> io::Result<Vec<u8>> {
    use std::os::unix::net::UnixStream;
    let Transport::Unix(path) = transport;
    let mut stream = UnixStream::connect(path)?;
    // A first-run core.ensure may pull several multi-architecture images on a
    // slow connection. Keep the exchange bounded without treating a normal
    // cold install as a failed guest.
    stream.set_read_timeout(Some(RESPONSE_TIMEOUT))?;
    stream.set_write_timeout(Some(std::time::Duration::from_secs(10)))?;
    stream.write_all(request)?;
    stream.write_all(b"\n")?;
    stream.flush()?;
    let mut response = Vec::new();
    stream
        .take(MAX_RESPONSE_BYTES + 1)
        .read_to_end(&mut response)?;
    Ok(response)
}

#[cfg(windows)]
fn exchange(transport: &Transport, request: &[u8]) -> io::Result<Vec<u8>> {
    let Transport::Wsl {
        executable,
        distribution,
    } = transport;
    if distribution.is_empty()
        || distribution.len() > 64
        || !distribution
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid private WSL distribution name",
        ));
    }
    let mut child = Command::new(executable)
        .no_console_window()
        .args([
            "--distribution",
            distribution,
            "--user",
            "root",
            "--exec",
            "/usr/local/bin/lemma-guestd",
            "request",
        ])
        .env("WSLENV", "LEMMA_GUEST_CAPABILITY_FILE/u")
        .env("LEMMA_GUEST_CAPABILITY_FILE", "/etc/lemma/guest.capability")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    child
        .stdin
        .take()
        .ok_or_else(|| io::Error::other("WSL stdin unavailable"))?
        .write_all(&[request, b"\n"].concat())?;
    let output = child.wait_with_output()?;
    if output.stdout.len() as u64 > MAX_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "WSL guest response exceeded 4 MiB",
        ));
    }
    if output.stdout.is_empty() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(io::Error::other(format!(
            "private WSL runtime failed: {}",
            detail.lines().next().unwrap_or("no response")
        )));
    }
    Ok(output.stdout)
}

fn local_root() -> PathBuf {
    if let Some(root) = std::env::var_os("LEMMA_LOCALD_ROOT") {
        return PathBuf::from(root);
    }
    #[cfg(unix)]
    {
        std::env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".lemma/local/run")
    }
    #[cfg(windows)]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Lemma/runtime")
    }
}

// The bridge speaks to the guest over a unix socket on macOS and over
// `wsl.exe --exec` on Windows, and only the first can be stood up in-process:
// these tests bind a real UnixListener and hand the bridge a 0600 capability
// file. There is no Windows equivalent to gate them against, so they are
// unix-only rather than skipped.
#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;
    use std::thread;
    use tempfile::tempdir;

    fn capability(root: &Path) -> PathBuf {
        let path = root.join("guest.capability");
        fs::write(&path, "a".repeat(64)).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        path
    }

    #[test]
    fn injects_private_capability_and_forwards_one_response() {
        let root = tempdir().unwrap();
        let socket = root.path().join("guest.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            let request: Value = serde_json::from_str(&line).unwrap();
            assert_eq!(request["capability"], "a".repeat(64));
            let mut writer = stream;
            writer
                .write_all(b"{\"ok\":true,\"result\":{\"status\":\"ready\"}}\n")
                .unwrap();
        });
        let config = BridgeConfig {
            capability_file: capability(root.path()),
            transport: Transport::Unix(socket),
        };
        let mut output = Vec::new();

        let ok = request(
            b"{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n".as_slice(),
            &mut output,
            &config,
        )
        .unwrap();

        server.join().unwrap();
        assert!(ok);
        assert_eq!(
            serde_json::from_slice::<Value>(&output).unwrap()["result"]["status"],
            "ready"
        );
    }

    #[test]
    fn rejects_public_or_caller_supplied_capabilities() {
        let root = tempdir().unwrap();
        let capability_path = capability(root.path());
        fs::set_permissions(&capability_path, fs::Permissions::from_mode(0o644)).unwrap();
        let config = BridgeConfig {
            capability_file: capability_path,
            transport: Transport::Unix(root.path().join("unused.sock")),
        };
        assert_eq!(
            request(
                b"{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n".as_slice(),
                Vec::new(),
                &config,
            )
            .unwrap_err()
            .kind(),
            io::ErrorKind::PermissionDenied
        );

        let private = capability(root.path());
        let config = BridgeConfig {
            capability_file: private,
            transport: Transport::Unix(root.path().join("unused.sock")),
        };
        assert_eq!(
            request(
                b"{\"version\":1,\"capability\":\"forged\",\"operation\":\"health\"}\n".as_slice(),
                Vec::new(),
                &config,
            )
            .unwrap_err()
            .kind(),
            io::ErrorKind::PermissionDenied
        );
    }
}
