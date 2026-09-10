//! Finding an executable on this machine, and asking its version.

use super::{Command, Duration, HashSet, Path, PathBuf, SetupProcessError, env, setup_process};

/// How long an agent gets to answer `--version`.
///
/// A ceiling on a hang, not a measurement of anything. The command itself takes
/// milliseconds warm and a couple of seconds cold -- but this runs while the
/// adapter cache is being downloaded and hashed and the other agents are being
/// probed, because landing that cache is exactly what triggers the re-probe.
///
/// At five seconds that was a coin toss on first launch after an update, and
/// losing it published Claude Code as unusable with a message blaming the user's
/// install. Nothing is slower for the larger budget: a healthy agent answers and
/// the loop exits, so this is only ever reached by one that never will.
pub(crate) const VERSION_PROBE_TIMEOUT: Duration = Duration::from_secs(30);

/// Why an agent's version is unknown.
///
/// The distinction is the whole point. "Did not answer in time" is about this
/// machine at this moment and is worth retrying in seconds; "would not run" is
/// about the installation and is not. They used to be the same `None`, so a busy
/// laptop and a broken agent produced the same sentence and the same
/// quarter-hour wait.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum VersionUnknown {
    TimedOut,
    Failed,
}

pub(crate) fn probe_version(
    executable: &Path,
    arguments: &[String],
) -> Result<String, VersionUnknown> {
    probe_version_within(executable, arguments, VERSION_PROBE_TIMEOUT)
}

pub(crate) fn probe_version_within(
    executable: &Path,
    arguments: &[String],
    timeout: Duration,
) -> Result<String, VersionUnknown> {
    let mut command = Command::new(executable);
    command.args(arguments);
    let output =
        setup_process::run(command, timeout, 1024 * 1024).map_err(|error| match error {
            SetupProcessError::TimedOut => VersionUnknown::TimedOut,
            SetupProcessError::OutputLimit
            | SetupProcessError::Io(_)
            | SetupProcessError::Cancelled => VersionUnknown::Failed,
        })?;
    if !output.status.success() {
        return Err(VersionUnknown::Failed);
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let text = if stdout.trim().is_empty() {
        stderr.trim().to_owned()
    } else {
        stdout.trim().to_owned()
    };
    (!text.is_empty())
        .then_some(text)
        .ok_or(VersionUnknown::Failed)
}

pub(crate) fn version_is_at_least(installed: &str, minimum: &str) -> bool {
    fn find_version(value: &str) -> Option<[u64; 3]> {
        value.split_whitespace().find_map(|token| {
            let token = token.trim_start_matches(|character: char| !character.is_ascii_digit());
            let token = token
                .split_once('-')
                .map_or(token, |(version, _)| version)
                .trim_end_matches(|character: char| {
                    !character.is_ascii_digit() && character != '.'
                });
            let mut parts = token.split('.');
            Some([
                parts.next()?.parse().ok()?,
                parts.next()?.parse().ok()?,
                parts.next()?.parse().ok()?,
            ])
        })
    }

    find_version(installed)
        .zip(find_version(minimum))
        .is_some_and(|(installed, minimum)| installed >= minimum)
}

pub(crate) fn resolve_executable(command: &str) -> Option<PathBuf> {
    let candidate = Path::new(command);
    if candidate.components().count() > 1 {
        return candidate.is_file().then(|| candidate.to_path_buf());
    }
    resolve_executable_in(command, executable_search_paths())
}

pub(crate) fn resolve_executable_in(
    command: &str,
    search_paths: impl IntoIterator<Item = PathBuf>,
) -> Option<PathBuf> {
    for directory in search_paths {
        let candidate = directory.join(command);
        if candidate.is_file() {
            return Some(candidate);
        }
        #[cfg(windows)]
        for extension in ["exe", "cmd", "bat"] {
            let candidate = directory.join(format!("{command}.{extension}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

pub(crate) fn executable_search_paths() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    let mut seen = HashSet::new();
    for variable in ["LEMMA_AGENT_HOST_PATH", "PATH"] {
        if let Some(value) = env::var_os(variable) {
            for path in env::split_paths(&value) {
                push_unique(&mut paths, &mut seen, path);
            }
        }
    }

    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from);
    if let Some(home) = home.as_ref() {
        for directory in home_executable_directories(home) {
            push_unique(&mut paths, &mut seen, directory);
        }
        for path in nvm_node_bins(home) {
            push_unique(&mut paths, &mut seen, path);
        }
    }

    #[cfg(unix)]
    for path in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
        push_unique(&mut paths, &mut seen, PathBuf::from(path));
    }
    paths
}

/// Where an agent lives when it is installed under a user's home directory.
///
/// Two kinds of directory. Most are toolchain and package-manager `bin` paths —
/// where an agent lands when installed *by* something. The rest are what these
/// agents' own installers create, which no package manager knows about:
/// `OpenCode`'s script writes to `~/.opencode/bin` and Claude Code's local
/// install uses `~/.claude/local`.
///
/// This list is the whole of detection in practice. The Agent Host is a sidecar
/// of a GUI app, so it inherits `/usr/bin:/bin:/usr/sbin:/sbin` and nothing
/// else — never a login shell's `PATH` — and none of these are on it.
pub(crate) fn home_executable_directories(home: &Path) -> Vec<PathBuf> {
    // The platform's own additions are a cfg-selected list rather than
    // cfg-gated pushes. Pushing needs a `mut` binding, and on a platform that
    // matches neither arm — Linux, which is what CI lints on — every mutation
    // compiles out and the `mut` becomes an `unused_mut` the lint gate treats
    // as an error. Selecting the list instead leaves nothing to be unused.
    #[cfg(target_os = "macos")]
    let platform: &[&str] = &["Library/pnpm"];
    #[cfg(windows)]
    let platform: &[&str] = &["AppData/Roaming/npm"];
    #[cfg(not(any(target_os = "macos", windows)))]
    let platform: &[&str] = &[];

    [
        ".local/bin",
        ".cargo/bin",
        ".volta/bin",
        ".asdf/shims",
        ".local/share/mise/shims",
        ".local/share/pnpm",
        ".bun/bin",
        ".npm-global/bin",
        ".opencode/bin",
        ".claude/local",
        ".codex/bin",
    ]
    .iter()
    .chain(platform)
    .map(|relative| home.join(relative))
    .collect()
}

pub(crate) fn nvm_node_bins(home: &Path) -> Vec<PathBuf> {
    let root = home.join(".nvm/versions/node");
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut versions = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let version = entry.file_name();
            let version = version.to_str()?.trim_start_matches('v');
            semver::Version::parse(version)
                .ok()
                .map(|version| (version, entry.path().join("bin")))
        })
        .collect::<Vec<_>>();
    versions.sort_unstable_by(|left, right| right.0.cmp(&left.0));
    versions.into_iter().map(|(_, path)| path).collect()
}

pub(crate) fn push_unique(paths: &mut Vec<PathBuf>, seen: &mut HashSet<PathBuf>, path: PathBuf) {
    if seen.insert(path.clone()) {
        paths.push(path);
    }
}
