use std::env;
use std::io;
use std::path::{Path, PathBuf};

#[cfg(unix)]
use interprocess::local_socket::GenericFilePath;
#[cfg(windows)]
use interprocess::local_socket::GenericNamespaced;
use interprocess::local_socket::{prelude::*, Name};

#[derive(Clone, Debug)]
pub struct LocalPaths {
    pub root: PathBuf,
    pub token: PathBuf,
    pub state: PathBuf,
    pub journal: PathBuf,
    pub log: PathBuf,
}

impl LocalPaths {
    pub fn discover() -> io::Result<Self> {
        if let Some(root) = env::var_os("LEMMA_LOCALD_ROOT").filter(|value| !value.is_empty()) {
            return Ok(Self::new(PathBuf::from(root)));
        }
        Ok(Self::new(Self::default_root()?))
    }

    /// Where an installation keeps its state when nobody has said otherwise.
    ///
    /// Split out of `discover` because more than one thing needs to recognise
    /// the default installation -- notably the Windows guest, which keeps its
    /// historic name there and a per-root one everywhere else.
    pub fn default_root() -> io::Result<PathBuf> {
        #[cfg(target_os = "macos")]
        let root = home_dir()?.join("Library/Application Support/Lemma/locald");

        // Joined segment by segment, not as "Lemma/locald". The path is hashed
        // into the control endpoint's name, and a forward slash there produced
        // a different string -- and so a different pipe -- from the same
        // directory spelled the way Windows spells it.
        #[cfg(target_os = "windows")]
        let root = env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "LOCALAPPDATA is not set"))?
            .join("Lemma")
            .join("locald");

        #[cfg(all(unix, not(target_os = "macos")))]
        let root = env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .unwrap_or(home_dir()?.join(".local/state"))
            .join("lemma/locald");

        Ok(root)
    }

    pub fn new(root: PathBuf) -> Self {
        Self {
            token: root.join("control.token"),
            state: root.join("state.json"),
            journal: root.join("events.jsonl"),
            log: root.join("locald.log"),
            root,
        }
    }

    pub fn ensure(&self) -> io::Result<()> {
        std::fs::create_dir_all(&self.root)?;
        set_private_dir(&self.root)
    }

    #[cfg(unix)]
    pub fn socket_path(&self) -> PathBuf {
        self.root.join("control.sock")
    }

    pub fn socket_name(&self) -> io::Result<Name<'_>> {
        #[cfg(unix)]
        {
            let path = self.socket_path();
            // `sun_path` is 104 bytes on macOS and 108 on Linux, and the
            // interprocess crate's own message for exceeding it -- "local
            // socket name length exceeds capacity of sun_path of sockaddr_un"
            // -- names a struct field and no path, so it reads as a bug in
            // Lemma rather than as something about where the state directory
            // is. The daemon then exits during startup and the app reports
            // "exit status: 1".
            //
            // Unreachable at the default root, which is ~60 characters plus a
            // username. Very reachable via LEMMA_LOCALD_ROOT, which is how the
            // test harnesses and every developer point an installation at a
            // temporary directory -- and macOS hands those out under
            // /private/var/folders with paths well over a hundred characters
            // before anything is appended.
            const SUN_PATH_LIMIT: usize = 104;
            let length = path.as_os_str().len();
            if length >= SUN_PATH_LIMIT {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!(
                        "the control socket path is {length} characters and the \
                         operating system allows at most {}: {}. Set \
                         LEMMA_LOCALD_ROOT to a shorter directory.",
                        SUN_PATH_LIMIT - 1,
                        path.display(),
                    ),
                ));
            }
            path.to_fs_name::<GenericFilePath>()
        }

        #[cfg(windows)]
        {
            locald_pipe_name(&self.root)
                .to_ns_name::<GenericNamespaced>()
                .map(Name::into_owned)
        }
    }
}

/// What the control endpoint is called on Windows.
///
/// Split out so one assertion can pin the whole name -- the literal and the
/// hash together. Both halves are duplicated in the desktop shell, which is
/// what opens this name; see the note on `stable_hash`.
#[cfg(windows)]
pub(crate) fn locald_pipe_name(root: &Path) -> String {
    format!(r"LOCAL\work.lemma.locald.{:016x}", stable_hash(root))
}

#[cfg(not(windows))]
fn home_dir() -> io::Result<PathBuf> {
    env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(PathBuf::from))
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "home directory is not set"))
}

/// A stable identity for a state root, for naming things keyed to it.
///
/// Normalised first: Windows paths are case-insensitive and accept either
/// separator, so the same directory can be spelled several ways and each
/// spelling used to hash differently -- which meant a daemon started without
/// LEMMA_LOCALD_ROOT could open an endpoint the app would never look for, and
/// the app would start a second daemon beside it.
#[cfg(windows)]
pub(crate) fn stable_hash(path: &Path) -> u64 {
    path.to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_ascii_lowercase()
        .bytes()
        .fold(0xcbf29ce484222325, |hash, byte| {
            (hash ^ u64::from(byte)).wrapping_mul(0x100000001b3)
        })
}

/// Move a file the daemon cannot parse aside instead of deleting it.
///
/// Every fatal read in `Daemon::new` used to end the process; healing them means
/// replacing the file, and replacing it must not destroy the only copy of
/// whatever went wrong. A support request can still ask for the `.invalid-`
/// sibling, and a downgrade that quarantines a newer schema can be recovered by
/// upgrading again.
///
/// The name matches `artifact_install::quarantine_path` in the desktop crate
/// byte for byte -- two processes write into the same tree and one convention
/// covers both. Nothing globs for these, so a collision only costs a name.
pub fn quarantine_aside(path: &Path) -> io::Result<PathBuf> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "cannot quarantine a path with no safe file name",
            )
        })?;
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| io::Error::other("system clock is before the unix epoch"))?
        .as_millis();
    let aside = path.with_file_name(format!(".{name}.invalid-{millis}"));
    std::fs::rename(path, &aside)?;
    Ok(aside)
}

/// The phrase that turns a daemon error into an offer to reset.
///
/// Anything a user cannot fix by retrying, but *can* fix by discarding local
/// data, says this. `runtime_operation_error_code` maps it to a stable code and
/// the splash renders the reset button for it, so a new detector needs no new
/// transport -- only this phrase in its message.
pub const DATA_RESET_MARKER: &str = "local data must be reset";

/// Name of the marker recording that this installation's data can no longer be
/// read by the credentials it now has.
const DATA_RESET_MARKER_FILE: &str = "data-reset-required";

/// Record that local data has been stranded, and why.
///
/// Written when a secret is replaced. Reminting `infra.secrets.json` does not
/// change the password baked into the Postgres volume at `initdb`, and
/// reminting `host.secrets.json` makes every encrypted column undecryptable --
/// so healing those files, on its own, would turn a loud failure into a silent
/// one. This is the deliberate exception to "a self-heal should soften the
/// failure": a hard stop with a button beats an install that quietly cannot
/// read its own data.
pub fn require_data_reset(root: &Path, reason: &str) -> io::Result<()> {
    std::fs::create_dir_all(root)?;
    std::fs::write(root.join(DATA_RESET_MARKER_FILE), format!("{reason}\n"))
}

/// Why this installation needs its data discarded, if it does.
pub fn data_reset_reason(root: &Path) -> Option<String> {
    let reason = std::fs::read_to_string(root.join(DATA_RESET_MARKER_FILE)).ok()?;
    let reason = reason.trim();
    Some(if reason.is_empty() {
        "this installation's private credentials were replaced".to_owned()
    } else {
        reason.to_owned()
    })
}

/// Has this installation ever held user data?
///
/// The question a *missing* secrets file raises. Both secret files carry the
/// same invariant -- they are gone when the data is gone -- so reminting one
/// beside a surviving data directory is what makes every encrypted row
/// permanently unreadable, silently. The corrupt case already records a reset;
/// the absent case fell straight through to minting, which is the same outcome
/// arrived at more quietly.
///
/// A file can go missing without the data going with it: a restore from a
/// backup that skipped dotfiles, a `cp -R` that dropped an owner-only file, a
/// half-finished manual cleanup.
///
/// Deliberately generous about what counts. A first run has an empty `data/`
/// tree and no disk, so this is false and nothing is said; anything that has
/// actually run has one or the other.
pub fn installation_has_data(root: &Path) -> bool {
    let populated =
        |path: PathBuf| std::fs::read_dir(path).is_ok_and(|mut entries| entries.next().is_some());
    // The managed disk is the strongest signal: it only exists once a runtime
    // has been prepared, and it holds the databases.
    if std::fs::read_dir(root.join("runtime")).is_ok_and(|entries| {
        entries
            .filter_map(Result::ok)
            .any(|entry| entry.path().join("data.raw").exists())
    }) {
        return true;
    }
    // Files and object storage live on the host side, outside the disk.
    ["data/files", "data/object-storage", "data/workspaces"]
        .iter()
        .any(|relative| populated(root.join(relative)))
}

/// Forget the marker. Only a completed data reset may call this.
pub fn clear_data_reset(root: &Path) -> io::Result<()> {
    match std::fs::remove_file(root.join(DATA_RESET_MARKER_FILE)) {
        Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error),
        _ => Ok(()),
    }
}

#[cfg(unix)]
fn set_private_dir(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
}

#[cfg(windows)]
fn set_private_dir(_path: &Path) -> io::Result<()> {
    // The per-user LocalAppData ACL is inherited. Protocol access also
    // requires the random capability stored in this directory.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The guest and the daemon must agree on the phrase, character for
    /// character.
    ///
    /// `lemma-guestd` is a Linux binary that ships inside the VM image and
    /// links nothing from this crate, so the constant is duplicated rather than
    /// shared. The phrase is the entire contract between them: guestd raises it
    /// when it finds a cluster it cannot open, this crate maps it to
    /// `local-data-incompatible`, and the splash renders a reset button for
    /// that code. Change it on one side only and the guest still fails -- with
    /// no button, which is the failure this whole path exists to remove.
    #[test]
    fn the_guest_raises_the_same_reset_phrase_this_daemon_maps() {
        let guestd = include_str!("../../local-runtime/guestd/src/lib.rs").replace("\r\n", "\n");
        assert!(
            guestd.contains(&format!(
                "DATA_RESET_MARKER: &str = \"{DATA_RESET_MARKER}\""
            )),
            "lemma-guestd must declare the same marker phrase as this crate",
        );
    }

    /// A first run mints its secrets quietly; an install that has data does not.
    ///
    /// Both secrets files carry the same invariant: they are gone when the data
    /// is gone. The corrupt case already records a reset. The *missing* case
    /// fell straight through to minting a replacement -- which, beside a
    /// surviving data directory, makes every encrypted row unreadable and the
    /// Postgres volume unopenable, with nothing said. A file can go missing
    /// without the data: a restore that skipped an owner-only file, a `cp -R`
    /// that dropped one, a half-finished cleanup.
    #[test]
    fn an_installation_that_has_run_is_told_apart_from_a_first_run() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path();

        // A first run: the tree exists and is empty.
        for relative in ["data/files", "data/object-storage", "data/workspaces"] {
            std::fs::create_dir_all(root.join(relative)).unwrap();
        }
        std::fs::create_dir_all(root.join("runtime/macos")).unwrap();
        assert!(
            !installation_has_data(root),
            "an empty tree is a first run and must mint without ceremony",
        );

        // One uploaded file is enough to make a reminted secret a loss.
        std::fs::write(root.join("data/files/anything"), b"x").unwrap();
        assert!(installation_has_data(root));
    }

    /// The managed disk counts on its own, because the databases are inside it.
    #[test]
    fn a_prepared_runtime_counts_as_data_even_with_an_empty_file_tree() {
        let root = tempfile::tempdir().unwrap();
        let root = root.path();
        std::fs::create_dir_all(root.join("data/files")).unwrap();
        std::fs::create_dir_all(root.join("runtime/macos")).unwrap();
        assert!(!installation_has_data(root));

        std::fs::write(root.join("runtime/macos/data.raw"), b"").unwrap();
        assert!(
            installation_has_data(root),
            "every table lives in this disk; a new password opens none of them",
        );
    }

    #[test]
    fn quarantine_moves_aside_without_destroying_the_original_bytes() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("operator-config.json");
        std::fs::write(&path, b"{ not json").unwrap();

        let aside = quarantine_aside(&path).unwrap();

        assert!(!path.exists(), "the unparseable file is out of the way");
        assert_eq!(std::fs::read(&aside).unwrap(), b"{ not json");
        assert_eq!(aside.parent(), path.parent());
        let name = aside.file_name().unwrap().to_str().unwrap();
        assert!(name.starts_with(".operator-config.json.invalid-"), "{name}");
    }

    #[test]
    fn quarantine_reports_a_missing_file_rather_than_pretending_it_moved() {
        let root = tempfile::tempdir().unwrap();
        let error = quarantine_aside(&root.path().join("absent")).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::NotFound);
    }

    /// A path too long for a Unix socket says so, and says what to do.
    ///
    /// Hit for real while testing the quit path: the daemon exited immediately
    /// with "local socket name length exceeds capacity of sun_path of
    /// sockaddr_un", which names a C struct field and no path at all. Every
    /// temporary directory macOS hands out lives under /private/var/folders
    /// and is already most of the budget, so this is what a harness pointed at
    /// one gets -- and what it used to get was a daemon that would not start
    /// for reasons it did not explain.
    #[cfg(unix)]
    #[test]
    fn a_socket_path_too_long_for_the_kernel_explains_itself() {
        let roomy = LocalPaths::new(PathBuf::from("/tmp/lemma-test"));
        assert!(roomy.socket_name().is_ok());

        let long = LocalPaths::new(PathBuf::from(format!("/tmp/{}", "d".repeat(120))));
        let error = long.socket_name().unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
        let message = error.to_string();
        assert!(message.contains("control socket path"), "{message}");
        assert!(message.contains("LEMMA_LOCALD_ROOT"), "{message}");
        // The path itself, because "too long" without it is not actionable.
        assert!(message.contains("dddd"), "{message}");
    }

    #[test]
    fn state_files_share_one_root() {
        let paths = LocalPaths::new(PathBuf::from("/tmp/lemma-locald-test"));
        assert_eq!(paths.token.parent(), Some(paths.root.as_path()));
        assert_eq!(paths.state.parent(), Some(paths.root.as_path()));
        assert_eq!(paths.journal.parent(), Some(paths.root.as_path()));
    }

    #[cfg(windows)]
    #[test]
    fn named_pipe_identity_is_stable_and_user_root_specific() {
        assert_eq!(stable_hash(Path::new("a")), stable_hash(Path::new("a")));
        assert_ne!(stable_hash(Path::new("a")), stable_hash(Path::new("b")));
    }

    /// The daemon and the shell must derive the same endpoint name.
    ///
    /// This exact assertion is duplicated in desktop/src/main.rs, over the same
    /// path and the same expected string, because the code that produces it is
    /// duplicated too and cannot cheaply be shared -- locald is a sidecar
    /// binary, not a library the app links. They drifted once: the shell hashed
    /// the root unnormalised while this side lowercased it, so on every default
    /// Windows install the app opened a pipe its own daemon never listened on.
    /// Changing this value without changing the other one is the bug.
    #[cfg(windows)]
    #[test]
    fn named_pipe_name_matches_the_one_the_desktop_shell_opens() {
        assert_eq!(
            locald_pipe_name(Path::new(r"C:\Users\Example\AppData\Local\Lemma\locald")),
            r"LOCAL\work.lemma.locald.a5c86f3cbfe10caf"
        );
        // Every spelling of one directory is one endpoint.
        assert_eq!(
            locald_pipe_name(Path::new(r"C:\Users\Example\AppData\Local\Lemma\locald")),
            locald_pipe_name(Path::new(r"c:/users/example/appdata/local/lemma/locald/"))
        );
    }
}
