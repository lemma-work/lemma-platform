//! The scripted agent shims a test runs instead of a real harness.

use super::*;

// ---------------------------------------------------------------------------
// Locating Python for the scripted ACP agent.
// ---------------------------------------------------------------------------

#[must_use]
pub fn python() -> PathBuf {
    let executable_names = if cfg!(windows) {
        &["python.exe", "python3.exe"][..]
    } else {
        &["python3", "python"][..]
    };
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .find_map(|path| {
            executable_names
                .iter()
                .map(|name| path.join(name))
                .find(|candidate| candidate.is_file())
        })
        .expect("Python is required for the Agent Host integration suite")
}

#[must_use]
pub fn scripted_agent_fixture() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("scripted_acp_agent.py")
}

// ---------------------------------------------------------------------------
// A shim that makes the pinned manifest resolve to the scripted ACP agent.
// ---------------------------------------------------------------------------

/// A directory that `LEMMA_AGENT_HOST_PATH` can point at so the host's *own*
/// adapter resolution picks the scripted agent.
///
/// The alternative — constructing a `ResolvedAdapter` by hand — skips
/// `HostRuntime`'s discovery, probe, publish, and MCP-bridge wiring, which is
/// most of what these tests exist to cover. Both native adapters are shimmed so
/// a developer machine with a real `opencode` on `PATH` cannot be probed by
/// accident.
pub struct ShimmedAgents {
    pub directory: PathBuf,
    pub acp_log: PathBuf,
    /// The manifest key the shim now resolves to.
    pub harness_key: String,
}

impl ShimmedAgents {
    /// `mode` is passed through to `scripted_acp_agent.py`.
    ///
    /// # Panics
    /// If the shim cannot be written or made executable.
    #[must_use]
    pub fn install(root: &Path, mode: &str) -> Self {
        let directory = root.join("shim-bin");
        std::fs::create_dir_all(&directory).unwrap();
        let acp_log = root.join(format!("acp-{mode}.jsonl"));
        let script = format!(
            "#!/bin/sh\n\
             case \"$1\" in\n\
             --version) echo '2026.7.31' ;;\n\
             *) exec {python} {fixture} {log} {mode} ;;\n\
             esac\n",
            python = shell_quote(&python()),
            fixture = shell_quote(&scripted_agent_fixture()),
            log = shell_quote(&acp_log),
            mode = mode,
        );
        // `cursor` is the harness under test; `opencode` is shimmed only so the
        // host's discovery never launches a real local agent.
        for name in ["cursor-agent", "opencode"] {
            let path = directory.join(name);
            std::fs::write(&path, &script).unwrap();
            make_executable(&path);
        }
        Self {
            directory,
            acp_log,
            harness_key: "cursor".to_owned(),
        }
    }

    /// Every ACP/MCP message the scripted agent saw or sent.
    ///
    /// # Panics
    /// If the log exists but is not valid JSONL.
    #[must_use]
    pub fn traffic(&self) -> Vec<Value> {
        std::fs::read_to_string(&self.acp_log)
            .unwrap_or_default()
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }
}

#[cfg(unix)]
fn make_executable(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

#[cfg(not(unix))]
fn make_executable(_path: &Path) {}

fn shell_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', r"'\''"))
}
