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
            let pipe_name = format!(r"LOCAL\work.lemma.locald.{:016x}", stable_hash(&self.root));
            pipe_name
                .to_ns_name::<GenericNamespaced>()
                .map(Name::into_owned)
        }
    }
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
}
