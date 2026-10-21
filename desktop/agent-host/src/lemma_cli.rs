//! Which `lemma` CLI a run's agent, and its commands, are given.
//!
//! The agent is told to use the `lemma` CLI, and the host lets that one binary
//! run without a permission request (`acp::session_options`). Which binary that
//! is depends on who the run is for:
//!
//! - **The Lemma installed on this computer** names its own: the host pack's,
//!   the same release as the server (`WORKSPACE_HOST_CLI_ROOT`, sent as the run's
//!   `mcp.lemma_cli`). Only this pairing's word is taken, because it is the only
//!   server that is on this Mac and knows what is here.
//! - **Every other pairing** -- a hosted workspace, a teammate's install -- is a
//!   server somewhere else. A path it names is a path on *its* machine, and
//!   letting a remote server pick which binary on this Mac runs unasked is
//!   exactly what must not happen. Those runs get the CLI Lemma Desktop ships
//!   inside the app, beside this binary; failing that, the one this host
//!   downloaded for its own release (`download`); or none.
//!
//! So the order is: the local install's named CLI (local pairing only), the
//! bundled one, the downloaded one, none. Either way the folder has to pass
//! `seatbelt::lemma_cli_root`, the rule host execution applies to every folder
//! it widens commands to. A folder that does not is no CLI at all: the agent
//! gets no allow rule rather than one naming something else.

pub mod download;

use std::path::{Path, PathBuf};

use serde_json::Value;

/// Names the bundled CLI's folder when running from source, where there is no
/// app bundle to find it in. Honoured by debug builds only, as Desktop's own
/// overrides are (`runtime_layout::dev_override`): a release reads the folder
/// from where it was installed and nowhere else.
pub const CLI_ROOT_OVERRIDE: &str = "LEMMA_AGENT_HOST_CLI_ROOT";

/// The folder, inside the app's resources, that Desktop's build stages the CLI
/// into (`scripts/build_local_host_pack.py --cli-only`).
pub const BUNDLED_FOLDER: &str = "lemma-cli";

/// Where the CLI bundled with this binary should be, before it is checked.
///
/// In the app this binary is `Contents/MacOS/lemma-agent-host` and the CLI is in
/// `Contents/Resources/lemma-cli`; elsewhere resources sit beside the binary.
#[must_use]
pub fn bundled_candidates(executable: &Path) -> Vec<PathBuf> {
    let Some(bin_dir) = executable.parent() else {
        return Vec::new();
    };
    let mut candidates = Vec::new();
    if cfg!(target_os = "macos")
        && let Some(contents) = bin_dir.parent()
    {
        candidates.push(contents.join("Resources").join(BUNDLED_FOLDER));
    }
    candidates.push(bin_dir.join(BUNDLED_FOLDER));
    candidates
}

/// The CLI root a run on this pairing is given, if there is one it may have.
///
/// `named_by_lemma` is the run's `mcp.lemma_cli`; `bundled` is where the CLI
/// shipped with this binary would be. Both are checked against `home` exactly
/// as host execution checks every folder.
#[must_use]
pub fn cli_root(
    local_pairing: bool,
    named_by_lemma: Option<&str>,
    bundled: &[PathBuf],
    home: &Path,
) -> Option<PathBuf> {
    let admitted = |raw: &str| crate::host_exec::seatbelt::lemma_cli_root(raw, home);
    if local_pairing && let Some(raw) = named_by_lemma {
        if let Some(root) = admitted(raw) {
            return Some(root);
        }
        tracing::warn!(
            cli = raw,
            "ignored a lemma CLI folder that is not one; trying the one bundled with the app"
        );
    }
    bundled
        .iter()
        .find_map(|candidate| candidate.to_str().and_then(admitted))
}

/// The `bin/` of the CLI for a run on this pairing, as `cli_root` decides it
/// from the run's MCP configuration, this binary's location and `$HOME`, and
/// failing those, the CLI downloaded into `data_dir`.
///
/// Only a hosted run waits for a download, and only briefly
/// (`download::RUN_START_WAIT`); the local pairing takes a downloaded CLI only
/// if one is already there, since its server names its own.
pub async fn cli_bin_for_run(local_pairing: bool, mcp: &Value, data_dir: &Path) -> Option<PathBuf> {
    let home = std::env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)?;
    let home = std::fs::canonicalize(&home).unwrap_or(home);
    let named = mcp.get("lemma_cli").and_then(Value::as_str);
    if let Some(root) = cli_root(local_pairing, named, &bundled_cli_candidates(), &home) {
        return Some(root.join("bin"));
    }
    let fetcher = download::Downloader::shared(data_dir);
    let downloaded = if local_pairing {
        fetcher.installed_dir()
    } else {
        let Some(root) = fetcher.ready_within(download::RUN_START_WAIT).await else {
            if fetcher.enabled() {
                tracing::warn!(
                    "this run has no lemma CLI: none is bundled with the app and the download is not ready"
                );
            }
            return None;
        };
        root
    };
    cli_root(false, None, &[downloaded], &home).map(|root| root.join("bin"))
}

/// Starts downloading the CLI for a hosted pairing when the app carries none,
/// so it is usually there before the pairing's first run.
pub fn prefetch_for_hosted_pairing(data_dir: &Path) {
    let bundled = bundled_cli_candidates();
    if bundled
        .iter()
        .any(|candidate| candidate.join("bin/lemma").is_file())
    {
        return;
    }
    download::Downloader::shared(data_dir).prefetch();
}

/// Where this process's bundled CLI may be: the development override, when
/// set and honoured, instead of the app bundle.
#[must_use]
pub fn bundled_cli_candidates() -> Vec<PathBuf> {
    if cfg!(debug_assertions)
        && let Some(root) = std::env::var_os(CLI_ROOT_OVERRIDE).filter(|value| !value.is_empty())
    {
        return vec![PathBuf::from(root)];
    }
    std::env::current_exe()
        .map(|executable| bundled_candidates(&executable))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cli_at(folder: &Path) -> PathBuf {
        std::fs::create_dir_all(folder.join("bin")).unwrap();
        std::fs::write(folder.join("bin/lemma"), "#!/bin/sh\n").unwrap();
        std::fs::canonicalize(folder).unwrap()
    }

    struct Machine {
        _directory: tempfile::TempDir,
        home: PathBuf,
        /// What the local install's server names: its host pack's CLI.
        named: PathBuf,
        /// The one shipped in the app.
        bundled: PathBuf,
    }

    fn machine() -> Machine {
        let directory = tempfile::tempdir().unwrap();
        let base = std::fs::canonicalize(directory.path()).unwrap();
        let home = base.join("home");
        std::fs::create_dir_all(&home).unwrap();
        let named = cli_at(
            &home
                .join(crate::host_exec::seatbelt::CLI_ROOT_EXCEPTION)
                .join("releases/7/backend"),
        );
        let bundled = cli_at(
            &base
                .join("Lemma.app/Contents/Resources")
                .join(BUNDLED_FOLDER),
        );
        Machine {
            _directory: directory,
            home,
            named,
            bundled,
        }
    }

    #[test]
    fn the_local_install_names_its_own_cli() {
        let mac = machine();
        let chosen = cli_root(
            true,
            mac.named.to_str(),
            std::slice::from_ref(&mac.bundled),
            &mac.home,
        );
        assert_eq!(chosen, Some(mac.named.clone()));
        // And with nothing named, the app's own is still a CLI of this release.
        assert_eq!(
            cli_root(true, None, std::slice::from_ref(&mac.bundled), &mac.home),
            Some(mac.bundled.clone())
        );
    }

    /// A server elsewhere names a path on its own machine. However real that
    /// path happens to be here, it is not this Mac's to run unasked.
    #[test]
    fn any_other_pairing_gets_the_bundled_cli_whatever_it_names() {
        let mac = machine();
        assert_eq!(
            cli_root(
                false,
                mac.named.to_str(),
                std::slice::from_ref(&mac.bundled),
                &mac.home
            ),
            Some(mac.bundled.clone())
        );
    }

    /// No CLI in the app, as in a build that did not stage one: no allow rule
    /// at all, rather than one naming whatever the server said.
    #[test]
    fn a_missing_bundle_gives_a_hosted_run_no_cli() {
        let mac = machine();
        let absent = mac.bundled.with_file_name("not-there");
        assert_eq!(
            cli_root(
                false,
                mac.named.to_str(),
                std::slice::from_ref(&absent),
                &mac.home
            ),
            None
        );
        assert_eq!(cli_root(false, None, &[], &mac.home), None);
        // A bundle without `bin/lemma` is not one either.
        std::fs::create_dir_all(&absent).unwrap();
        assert_eq!(cli_root(false, None, &[absent], &mac.home), None);
    }

    /// The bundled folder is held to the same rule as any other: one inside
    /// the owner's secrets is refused wherever it came from.
    #[test]
    fn a_bundled_folder_is_checked_like_any_other() {
        let mac = machine();
        let inside_secrets = cli_at(&mac.home.join(".ssh/lemma-cli"));
        assert_eq!(cli_root(false, None, &[inside_secrets], &mac.home), None);
    }

    /// Where the host keeps a downloaded CLI by default: inside Lemma's own
    /// application data, which is denied to commands except for this folder.
    fn downloaded_at(home: &Path) -> PathBuf {
        cli_at(
            &home
                .join(crate::host_exec::seatbelt::DOWNLOADED_CLI_EXCEPTION)
                .join("9.8.7"),
        )
    }

    /// Named (local pairing only), then bundled, then downloaded, then none.
    #[test]
    fn the_resolution_order_is_named_bundled_downloaded() {
        let mac = machine();
        let downloaded = downloaded_at(&mac.home);
        let both = [mac.bundled.clone(), downloaded.clone()];
        assert_eq!(
            cli_root(true, mac.named.to_str(), &both, &mac.home),
            Some(mac.named.clone())
        );
        assert_eq!(
            cli_root(false, mac.named.to_str(), &both, &mac.home),
            Some(mac.bundled.clone())
        );
        let absent = mac.bundled.with_file_name("not-there");
        assert_eq!(
            cli_root(
                false,
                mac.named.to_str(),
                &[absent.clone(), downloaded.clone()],
                &mac.home
            ),
            Some(downloaded.clone())
        );
        assert_eq!(
            cli_root(
                true,
                Some("/does/not/exist"),
                &[absent, downloaded.clone()],
                &mac.home
            ),
            Some(downloaded)
        );
    }

    /// The rest of Lemma's application data stays refused: only the
    /// downloaded CLIs' own folder is carved out of it.
    #[test]
    fn only_the_download_folder_is_carved_out_of_lemmas_data() {
        let mac = machine();
        let beside = cli_at(
            &mac.home
                .join("Library/Application Support/Lemma/agent-host/adapters/x"),
        );
        assert_eq!(cli_root(false, None, &[beside], &mac.home), None);
    }

    #[test]
    fn a_named_folder_that_is_not_a_cli_falls_back_to_the_bundled_one() {
        let mac = machine();
        assert_eq!(
            cli_root(
                true,
                Some("/does/not/exist"),
                std::slice::from_ref(&mac.bundled),
                &mac.home
            ),
            Some(mac.bundled.clone())
        );
    }

    #[test]
    fn the_bundle_is_looked_for_where_the_app_puts_resources() {
        let candidates = bundled_candidates(Path::new(
            "/Applications/Lemma.app/Contents/MacOS/lemma-agent-host",
        ));
        let beside = PathBuf::from("/Applications/Lemma.app/Contents/MacOS/lemma-cli");
        if cfg!(target_os = "macos") {
            assert_eq!(
                candidates,
                [
                    PathBuf::from("/Applications/Lemma.app/Contents/Resources/lemma-cli"),
                    beside
                ]
            );
        } else {
            assert_eq!(candidates, [beside]);
        }
    }
}
