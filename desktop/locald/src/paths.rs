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
            self.socket_path().to_fs_name::<GenericFilePath>()
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
