use super::*;

pub(crate) fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .expect("HOME/USERPROFILE is not set")
}

pub(crate) fn app_support_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("LEMMA_DESKTOP_APP_SUPPORT_DIR") {
        return PathBuf::from(path);
    }
    #[cfg(target_os = "macos")]
    {
        home_dir()
            .join("Library/Application Support")
            .join(DATA_DIR_NAME)
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(home_dir)
            .join(DATA_DIR_NAME)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        std::env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".local/state"))
            .join("lemma")
    }
}

pub(crate) fn locald_root() -> PathBuf {
    std::env::var_os("LEMMA_LOCALD_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| app_support_dir().join("locald"))
}

pub(crate) fn runtime_install_root() -> PathBuf {
    app_support_dir().join("runtime")
}

pub(crate) fn install_log_path() -> PathBuf {
    runtime_install_root().join("install.log")
}

pub(crate) fn launch_log_path() -> PathBuf {
    runtime_install_root().join("launch.log")
}

/// Where the daemon's own stderr goes.
///
/// Deliberately its own file rather than `install.log`. `append_bounded_log`
/// rotates by renaming, and a child holding an inherited descriptor keeps
/// writing to the renamed inode -- so sharing a file would silently split the
/// record exactly when someone is reading it.
pub(crate) fn locald_stderr_path() -> PathBuf {
    runtime_install_root().join("locald-stderr.log")
}

/// A sink for locald's stderr that cannot block the daemon.
///
/// This used to be `Stdio::null()`, which meant every fatal `Daemon::new`
/// failure -- a malformed control token, an unreadable operator config, a port
/// that could not be reserved -- was discarded, and the user was shown only
/// "lemma-locald exited during startup (exit status: 1)".
///
/// A file, never `Stdio::piped()`: nothing in this process would drain a pipe,
/// and a full pipe buffer blocks the writer. Falls back to `null()` rather than
/// failing the spawn, because not having a log is not a reason to have no
/// daemon.
pub(crate) fn locald_stderr_sink() -> Stdio {
    let path = locald_stderr_path();
    let Some(parent) = path.parent() else {
        return Stdio::null();
    };
    if std::fs::create_dir_all(parent).is_err() {
        return Stdio::null();
    }
    // Truncate per spawn: this file exists to explain *this* launch, and a
    // stale reason from a previous run is worse than none.
    match std::fs::File::create(&path) {
        Ok(file) => Stdio::from(file),
        Err(_) => Stdio::null(),
    }
}

/// The last thing locald said before it died, for the message the user sees.
///
/// Bounded read from the tail: this is an error path and the file is normally
/// empty, but a `cargo run` fallback in a source checkout puts compiler output
/// here and that can be large.
pub(crate) fn locald_stderr_tail() -> Option<String> {
    const MAX_TAIL_BYTES: usize = 4096;
    let raw = std::fs::read(locald_stderr_path()).ok()?;
    let start = raw.len().saturating_sub(MAX_TAIL_BYTES);
    let tail = String::from_utf8_lossy(&raw[start..]);
    tail.lines()
        .rev()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(|line| line.trim_start_matches("lemma-locald: ").to_owned())
}

/// Record how long a launch stage took.
///
/// "Opens instantly" is not a claim anyone can check by feel — a resumed launch
/// and a splash launch look the same in a screen recording once both have
/// finished. This writes the actual milliseconds for each stage so a regression
/// shows up as a number, and so the startup targets have evidence behind them
/// rather than a stopwatch and an opinion.
pub(crate) fn launch_trace(stage: &str) {
    let elapsed = LAUNCH_START.get_or_init(Instant::now).elapsed().as_millis();
    append_bounded_log(&launch_log_path(), &format!("{elapsed:>6}ms {stage}"));
}

pub(crate) fn operation_id(prefix: &str) -> String {
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("{prefix}-{}-{nonce}", std::process::id())
}

pub(crate) fn append_install_log(message: &str) {
    append_bounded_log(&install_log_path(), message);
}

/// Append one timestamped line, rotating once the file reaches its ceiling.
///
/// Best-effort throughout: a log that cannot be written must never be the
/// reason an install, a launch, or a shutdown fails.
pub(crate) fn append_bounded_log(path: &std::path::Path, message: &str) {
    let Some(parent) = path.parent() else {
        return;
    };
    if std::fs::create_dir_all(parent).is_err() {
        return;
    }
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= MAX_INSTALL_LOG_BYTES)
    {
        let previous = path.with_extension("previous.log");
        let _ = std::fs::remove_file(&previous);
        let _ = std::fs::rename(path, previous);
    }
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let Ok(mut file) = options.open(path) else {
        return;
    };
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let clean = message.replace(['\r', '\n'], " ");
    let _ = writeln!(file, "{timestamp} {clean}");
}

pub(crate) fn locald_socket_name(root: &std::path::Path) -> Result<Name<'_>, String> {
    #[cfg(unix)]
    {
        root.join("control.sock")
            .to_fs_name::<GenericFilePath>()
            .map_err(|error| error.to_string())
    }
    #[cfg(windows)]
    {
        locald_pipe_name(root)
            .to_ns_name::<GenericNamespaced>()
            .map(Name::into_owned)
            .map_err(|error| error.to_string())
    }
}

/// What the control endpoint is called on Windows.
///
/// Split out so one assertion can pin the whole name -- the literal and the
/// hash together. Both halves are duplicated in locald, and it was the hash
/// half that drifted.
#[cfg(windows)]
pub(crate) fn locald_pipe_name(root: &std::path::Path) -> String {
    format!(r"LOCAL\work.lemma.locald.{:016x}", stable_hash(root))
}

/// A stable identity for a state root, for naming things keyed to it.
///
/// This has to stay byte-for-byte identical to `LocalPaths::stable_hash` in
/// locald/src/paths.rs, because the two are the only things that decide what
/// the control endpoint is called: the app opens the name this produces, and
/// the daemon listens on the name that one produces. locald is a sidecar
/// binary, not a library this crate links -- pulling in hyper, tokio, keyring
/// and reqwest to share eight lines would cost more than the app's whole
/// payload budget -- so the code is duplicated and pinned instead. Both copies
/// carry the same golden test over the same path, so a change to either one
/// fails its own crate's suite rather than shipping.
///
/// Normalised first: Windows paths are case-insensitive and accept either
/// separator, so the same directory can be spelled several ways and each
/// spelling used to hash differently. The daemon has normalised since that was
/// found; this side did not, and `%LOCALAPPDATA%` always contains uppercase --
/// so on every default Windows install the app looked for a pipe the daemon it
/// had just spawned was never going to open, and spent the whole 45s start
/// budget failing to connect to a process that was running fine.
#[cfg(windows)]
pub(crate) fn stable_hash(path: &std::path::Path) -> u64 {
    path.to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_ascii_lowercase()
        .bytes()
        .fold(0xcbf29ce484222325, |hash, byte| {
            (hash ^ u64::from(byte)).wrapping_mul(0x100000001b3)
        })
}

pub(crate) fn config_path() -> PathBuf {
    app_support_dir().join("desktop-config.json")
}

pub(crate) fn read_config() -> Value {
    std::fs::read_to_string(config_path())
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_else(|| json!({}))
}

pub(crate) fn write_config(update: impl FnOnce(&mut Value)) -> Result<(), String> {
    config_store::update(&config_path(), update)
        .map_err(|error| format!("could not save desktop configuration: {error}"))
}

pub(crate) fn path_identity(path: &std::path::Path) -> String {
    std::fs::canonicalize(path)
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

/// Which *build* of an executable is at a path, not just which path it is.
///
/// A path is not an identity when the file at it can be replaced. On macOS an
/// update moves the old app to the Trash, so the running daemon's path changes
/// and comparing paths is enough. On Windows an in-place update writes to the
/// same path -- so a daemon from the previous version reports an identical
/// path, and a shell that compares only paths adopts it: the new app
/// supervising the old runtime, with the same version on both sides and
/// nothing anywhere saying so.
///
/// Size and modification time rather than a content hash. The daemon sends
/// this on every handshake and the shell computes it on every connect, and
/// reading thirty megabytes to answer "is this the file I ship" is not a cost
/// worth paying for a question a replaced file already answers: an installer
/// that writes a new file changes both.
///
/// `None` when the file cannot be measured, which is itself a different
/// daemon -- the executable it is running from is gone.
pub(crate) fn executable_stamp(path: &std::path::Path) -> Option<(u64, u128)> {
    let resolved = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let metadata = std::fs::metadata(&resolved).ok()?;
    let modified = metadata
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_millis();
    Some((metadata.len(), modified))
}

/// The storage partition a server's session lives in.
///
/// Lemma Cloud and a local install are different servers -- different accounts,
/// different databases, different signing keys -- shown in one window. They are
/// also different origins (`lemma.work` versus `app.lemma.localhost`), so the
/// browser's own rules already stop either reading the other's cookies.
///
/// What the origin rules do *not* do is bound a session's lifetime to the
/// server that issued it. `app.lemma.localhost` is a stable hostname reused by
/// every local installation this machine ever has, and cookies ignore the port,
/// so a session minted against one local database is still presented to the
/// next one -- which rejects it, correctly, on every authorized route while
/// `/auth/session/refresh` keeps answering 200 because the refresh token itself
/// is genuinely valid. One install was measured writing 8 MB of backend log an
/// hour in that state, indefinitely.
///
/// Giving each server its own store is what makes the two independent rather
/// than merely non-overlapping. The earlier version of this fix cleared the
/// single shared store whenever the mode changed, which signed the user out of
/// the server they were leaving *and* out of the one they were returning to --
/// and still did not fix the case above, which needs no mode change at all.
///
/// The identifiers are constants, not derived: they have to name the same store
/// on every launch, or a restart would look like a new server and lose the
/// session it was meant to keep.
#[cfg(target_os = "macos")]
pub(crate) fn session_partition_id(mode: &str) -> [u8; 16] {
    // Arbitrary, fixed, and distinct. Never reuse or reorder these.
    const HOSTED: [u8; 16] = *b"lemma.cloud.sess";
    const LOCAL: [u8; 16] = *b"lemma.local.sess";
    if mode == "hosted" {
        HOSTED
    } else {
        LOCAL
    }
}

/// The Windows spelling of the same idea. WebView2 partitions by user-data
/// folder rather than by identifier, so the two servers get two directories.
#[cfg(target_os = "windows")]
pub(crate) fn session_partition_dir(mode: &str) -> PathBuf {
    let name = if mode == "hosted" { "hosted" } else { "local" };
    app_support_dir().join("webview").join(name)
}
