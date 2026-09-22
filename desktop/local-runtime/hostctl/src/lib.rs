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
        let capability_file = required_path(
            "LEMMA_GUEST_CAPABILITY_FILE",
            std::env::var_os("LEMMA_GUEST_CAPABILITY_FILE"),
        )?;
        #[cfg(unix)]
        let transport = Transport::Unix(required_path(
            "LEMMA_GUEST_CONTROL_SOCKET",
            std::env::var_os("LEMMA_GUEST_CONTROL_SOCKET"),
        )?);
        #[cfg(windows)]
        let transport = Transport::Wsl {
            executable: std::env::var_os("LEMMA_WSL_BIN")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("wsl.exe")),
            distribution: wsl_distribution(std::env::var_os("LEMMA_WSL_DISTRIBUTION"))?,
        };
        Ok(Self {
            capability_file,
            transport,
        })
    }
}

/// A path the bridge must be told, never one it works out for itself.
///
/// There used to be a fallback under `~/.lemma/local/run` (or
/// `$LEMMA_LOCALD_ROOT`). It never matched the runtime manager, which keeps
/// both files under `<state root>/local/run` in the platform's application
/// data directory, so a bridge started without these variables failed with a
/// bare "No such file or directory" that named neither the file nor how to
/// point at the right one. locald always sets both; anyone running the
/// bridge by hand should get told which variable to set.
fn required_path(variable: &str, configured: Option<std::ffi::OsString>) -> io::Result<PathBuf> {
    match configured {
        Some(value) if !value.is_empty() => Ok(PathBuf::from(value)),
        Some(_) => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{variable} is empty"),
        )),
        None => Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "{variable} is not set; lemma-locald sets it for the backend, and a \
                 bridge run by hand must be given the path explicitly"
            ),
        )),
    }
}

/// The same error, now saying which file it was about.
fn at_path(path: &Path, what: &str, error: io::Error) -> io::Error {
    io::Error::new(error.kind(), format!("{what} {}: {error}", path.display()))
}

/// Which private distribution this bridge addresses.
///
/// Deliberately without a default. The name is per-installation on purpose:
/// the runtime manager derives it from the state root so that a second user
/// profile, a development root, or a reinstall pointed somewhere else each get
/// a guest of their own. The literal `"LemmaRuntime"` fallback that used to be
/// here undid that from the other end -- a bridge started without the variable
/// addressed whatever distribution happens to carry that name, which is
/// another installation's guest: its capability file, its containers, its data
/// disk.
///
/// An empty value is refused for a sharper reason than tidiness.
/// `wsl --distribution ""` does not fail; it selects the machine's *default*
/// distribution. That is the user's own Ubuntu, and Lemma would run guest
/// commands inside it as root.
#[cfg(any(windows, test))]
fn wsl_distribution(configured: Option<std::ffi::OsString>) -> io::Result<String> {
    let Some(name) = configured else {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "LEMMA_WSL_DISTRIBUTION is not set, so the runtime bridge cannot \
             tell which installation's guest it is meant to address",
        ));
    };
    let name = name.into_string().map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "LEMMA_WSL_DISTRIBUTION is not valid UTF-8",
        )
    })?;
    if name.trim().is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "LEMMA_WSL_DISTRIBUTION is empty; refusing to fall back to this \
             machine's default WSL distribution",
        ));
    }
    Ok(name)
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
        let metadata =
            fs::symlink_metadata(path).map_err(|error| at_path(path, "guest capability", error))?;
        if !metadata.file_type().is_file() || metadata.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "guest capability {} must be a private regular file",
                    path.display()
                ),
            ));
        }
    }
    let value =
        fs::read_to_string(path).map_err(|error| at_path(path, "guest capability", error))?;
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
    let mut stream =
        UnixStream::connect(path).map_err(|error| at_path(path, "guest control socket", error))?;
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

// The bridge speaks to the guest over a unix socket on macOS and over
// `wsl.exe --exec` on Windows, and only the first can be stood up in-process:
// these tests bind a real UnixListener and hand the bridge a 0600 capability
// file. There is no Windows equivalent to gate them against, so they are
// unix-only rather than skipped.
#[cfg(all(test, unix))]
mod tests {

    /// Join the server thread, or fail rather than hang the binary.
    ///
    /// It blocks in `accept()`; if the client under test never connects, an
    /// unconditional `join()` waits forever and takes every remaining test with
    /// it. `lemma-locald` has the same helper for the same reason -- a separate
    /// crate, and one function does not justify sharing one.
    fn join_within<T>(handle: std::thread::JoinHandle<T>, what: &str) -> T {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
        while std::time::Instant::now() < deadline {
            if handle.is_finished() {
                return handle.join().expect("the server thread panicked");
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        panic!("{what} never finished; it is still blocked on the socket");
    }
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

        join_within(server, "the stand-in guest");
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

    /// The distribution name is per-installation so that two installations do
    /// not quietly share one guest. A literal default here reintroduced that
    /// from the bridge's side, which is the half nothing checked.
    #[test]
    fn an_unset_distribution_is_refused_rather_than_guessed() {
        let error = wsl_distribution(None).expect_err("there is no safe guess");
        assert_eq!(error.kind(), io::ErrorKind::NotFound);
        assert!(
            !error.to_string().contains("LemmaRuntime"),
            "no default to name: {error}"
        );
    }

    /// Not tidiness: `wsl --distribution ""` succeeds and selects the
    /// machine's default distribution -- the user's own Ubuntu -- where Lemma
    /// would then run guest commands as root.
    #[test]
    fn an_empty_distribution_never_becomes_the_machine_default() {
        for value in ["", "   "] {
            let error = wsl_distribution(Some(std::ffi::OsString::from(value)))
                .expect_err("an empty name must not reach wsl.exe");
            assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
        }
    }

    #[test]
    fn a_configured_distribution_is_used_as_given() {
        assert_eq!(
            wsl_distribution(Some(std::ffi::OsString::from("LemmaRuntime-dev"))).unwrap(),
            "LemmaRuntime-dev"
        );
    }

    /// The fallback these replaced never matched where the runtime manager
    /// keeps the files, so it only ever produced a bare ENOENT.
    #[test]
    fn an_unset_path_is_refused_by_name_rather_than_guessed() {
        let error = required_path("LEMMA_GUEST_CONTROL_SOCKET", None)
            .expect_err("there is no correct default");
        assert_eq!(error.kind(), io::ErrorKind::NotFound);
        assert!(
            error.to_string().contains("LEMMA_GUEST_CONTROL_SOCKET"),
            "the error says which variable to set: {error}"
        );
        let error = required_path("LEMMA_GUEST_CAPABILITY_FILE", Some("".into()))
            .expect_err("an empty path names nothing");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
        assert_eq!(
            required_path(
                "LEMMA_GUEST_CAPABILITY_FILE",
                Some("/x/guest.capability".into())
            )
            .unwrap(),
            PathBuf::from("/x/guest.capability")
        );
    }

    #[test]
    fn a_missing_socket_or_capability_is_reported_with_its_path() {
        let root = tempdir().unwrap();
        let socket = root.path().join("nobody-listening.sock");
        let config = BridgeConfig {
            capability_file: capability(root.path()),
            transport: Transport::Unix(socket.clone()),
        };
        let error = request(
            b"{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n".as_slice(),
            Vec::new(),
            &config,
        )
        .expect_err("nothing is listening");
        assert_eq!(
            error.kind(),
            io::ErrorKind::NotFound,
            "the kind survives: {error}"
        );
        assert!(
            error.to_string().contains(&socket.display().to_string()),
            "the error names the socket it tried: {error}"
        );

        let missing = root.path().join("absent.capability");
        let config = BridgeConfig {
            capability_file: missing.clone(),
            transport: Transport::Unix(socket),
        };
        let error = request(
            b"{\"version\":1,\"operation\":\"health\",\"parameters\":{}}\n".as_slice(),
            Vec::new(),
            &config,
        )
        .expect_err("there is no capability");
        assert_eq!(error.kind(), io::ErrorKind::NotFound);
        assert!(
            error.to_string().contains(&missing.display().to_string()),
            "the error names the capability file it tried: {error}"
        );
    }
}
