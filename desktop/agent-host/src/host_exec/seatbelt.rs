//! The Seatbelt profile an exec-server runs under, and how it is invoked.
//!
//! The profile is compiled into the binary rather than shipped beside it, as
//! the adapter manifest is: nothing has to be packaged, a sandboxed process
//! cannot rewrite the policy it will be confined by next time, and the profile
//! a test proves is byte for byte the one that runs.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// `resources/host-sandbox.sb`.
pub const PROFILE: &str = include_str!("../../resources/host-sandbox.sb");

/// Where macOS keeps the sandbox launcher.
pub const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";

/// The most granted folders the profile has parameters for (`GRANT_0` ..
/// `GRANT_7`). A workspace asking for more is refused rather than silently
/// given fewer.
pub const MAX_GRANTS: usize = 8;

/// The most symbolic-link targets of credential paths the profile has
/// parameters for (`RESOLVED_0` .. `RESOLVED_15`).
pub const MAX_RESOLVED: usize = 16;

/// Every credential path the profile denies under `HOME`, relative to it.
///
/// The profile names them itself; this copy exists only to find the ones that
/// are symbolic links, whose targets the kernel matches instead (see
/// `resolved_credentials`). A test holds the two lists together.
pub const CREDENTIAL_PATHS: &[&str] = &[
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gcloud",
    ".azure",
    ".kube",
    ".docker/config.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".config/git/credentials",
    ".pgpass",
    ".my.cnf",
    ".vault-token",
    ".cargo/credentials",
    ".cargo/credentials.toml",
    ".gem/credentials",
    ".terraform.d",
    ".config/op",
    ".password-store",
    ".codex/auth.json",
    ".claude/.credentials.json",
    ".zsh_history",
    ".bash_history",
    ".lemma",
    "Library/Keychains",
    "Library/Application Support/Lemma",
];

/// Whether this machine can confine commands at all.
#[must_use]
pub fn available() -> bool {
    cfg!(target_os = "macos") && Path::new(SANDBOX_EXEC).is_file()
}

/// What one exec-server is confined to. Every path is canonical: the kernel
/// matches real paths, so `/var/...` has to arrive as `/private/var/...`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Confinement {
    pub root: PathBuf,
    pub home: PathBuf,
    /// This exec-server's own `$TMPDIR`.
    pub tmp: PathBuf,
    /// The owner's per-user temporary folder, which `mktemp` and the system
    /// frameworks use whatever `$TMPDIR` says.
    pub user_tmp: PathBuf,
    /// Where package managers keep their caches for host commands, instead of
    /// the owner's own (`cache_environment`).
    pub cache: PathBuf,
    pub grants: Vec<PathBuf>,
}

impl Confinement {
    /// `sandbox-exec`'s own arguments, up to and not including the command.
    ///
    /// `-p` rather than `-f`: the profile is in this binary, and writing it to
    /// a file first would only add a file for something to tamper with.
    #[must_use]
    pub fn sandbox_arguments(&self) -> Vec<OsString> {
        let mut arguments: Vec<OsString> = vec!["-p".into(), PROFILE.into()];
        let mut define = |name: &str, value: &Path| {
            arguments.push("-D".into());
            let mut pair = OsString::from(format!("{name}="));
            pair.push(value.as_os_str());
            arguments.push(pair);
        };
        define("ROOT", &self.root);
        define("HOME", &self.home);
        define("TMP", &self.tmp);
        define("USER_TMP", &self.user_tmp);
        define("CACHE", &self.cache);
        let grants: Vec<&PathBuf> = self.grants.iter().take(MAX_GRANTS).collect();
        for (index, grant) in grants.iter().enumerate() {
            define(&format!("GRANT_{index}"), grant);
        }
        for (index, git) in existing_repositories(&self.root, &grants)
            .iter()
            .enumerate()
        {
            define(&format!("GIT_{index}"), git);
        }
        for (index, resolved) in resolved_credentials(&self.home).iter().enumerate() {
            define(&format!("RESOLVED_{index}"), resolved);
        }
        arguments
    }
}

/// The `.git` of the root and of each grant, where one exists now.
///
/// Read when an exec-server starts, not when a workspace is chosen, so a
/// repository a command made becomes the owner's to keep from the next start
/// on -- and a folder gaining one does not count as a different workspace.
fn existing_repositories(root: &Path, grants: &[&PathBuf]) -> Vec<PathBuf> {
    std::iter::once(root)
        .chain(grants.iter().map(|grant| grant.as_path()))
        .map(|folder| folder.join(".git"))
        .filter(|git| std::fs::symlink_metadata(git).is_ok())
        .collect()
}

/// Where each credential path really is, for the ones that are symbolic
/// links somewhere else. The kernel matches the resolved path, so a denial of
/// `~/.ssh` alone does nothing for a `~/.ssh` that links into a dotfiles
/// repository.
#[must_use]
pub fn resolved_credentials(home: &Path) -> Vec<PathBuf> {
    let mut resolved = Vec::new();
    for relative in CREDENTIAL_PATHS {
        let path = home.join(relative);
        let Ok(real) = std::fs::canonicalize(&path) else {
            continue;
        };
        if real != path && !resolved.contains(&real) {
            resolved.push(real);
        }
    }
    if resolved.len() > MAX_RESOLVED {
        tracing::warn!(
            count = resolved.len(),
            "more linked credential folders than the sandbox profile can deny; the rest stay readable"
        );
        resolved.truncate(MAX_RESOLVED);
    }
    resolved
}

/// The environment that sends package managers to `cache` rather than to the
/// owner's own caches, which the profile does not let a command write -- and
/// which the owner's unconfined terminal would otherwise run code out of.
#[must_use]
pub fn cache_environment(cache: &Path) -> Vec<(&'static str, PathBuf)> {
    vec![
        ("XDG_CACHE_HOME", cache.to_path_buf()),
        ("npm_config_cache", cache.join("npm")),
        ("npm_config_store_dir", cache.join("pnpm-store")),
        ("npm_config_devdir", cache.join("node-gyp")),
        ("YARN_CACHE_FOLDER", cache.join("yarn")),
        ("BUN_INSTALL_CACHE_DIR", cache.join("bun")),
        ("COREPACK_HOME", cache.join("corepack")),
        ("DENO_DIR", cache.join("deno")),
        ("PIP_CACHE_DIR", cache.join("pip")),
        ("UV_CACHE_DIR", cache.join("uv")),
        ("POETRY_CACHE_DIR", cache.join("poetry")),
        ("CARGO_HOME", cache.join("cargo")),
        ("GOMODCACHE", cache.join("go/mod")),
        ("GOCACHE", cache.join("go/build")),
        ("GRADLE_USER_HOME", cache.join("gradle")),
        ("CLANG_MODULE_CACHE_PATH", cache.join("clang")),
        // `git init` copies its template's hooks into `.git/hooks`, which the
        // profile keeps read-only; an empty template makes none.
        ("GIT_TEMPLATE_DIR", cache.join("git-template")),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_grant_the_profile_can_express_is_passed_and_no_more() {
        let confinement = Confinement {
            root: "/r".into(),
            home: "/h".into(),
            tmp: "/t".into(),
            user_tmp: "/u".into(),
            cache: "/c".into(),
            grants: (0..10)
                .map(|index| PathBuf::from(format!("/g{index}")))
                .collect(),
        };
        let arguments = confinement.sandbox_arguments();
        let defines: Vec<_> = arguments
            .iter()
            .filter_map(|argument| argument.to_str())
            .filter(|argument| argument.contains('='))
            .collect();
        assert!(defines.contains(&"ROOT=/r"));
        assert!(defines.contains(&"GRANT_7=/g7"));
        assert!(!defines.iter().any(|define| define.starts_with("GRANT_8")));
        for index in 0..MAX_GRANTS {
            assert!(
                PROFILE.contains(&format!("(param \"GRANT_{index}\")")),
                "the profile has no rule for GRANT_{index}"
            );
        }
        for index in 0..=MAX_GRANTS {
            assert!(
                PROFILE.contains(&format!("(param \"GIT_{index}\")")),
                "the profile has no rule for GIT_{index}"
            );
        }
        for index in 0..MAX_RESOLVED {
            assert!(
                PROFILE.contains(&format!("\"RESOLVED_{index}\"")),
                "the profile has no rule for RESOLVED_{index}"
            );
        }
    }

    #[test]
    fn every_credential_path_resolved_here_is_one_the_profile_denies() {
        for relative in CREDENTIAL_PATHS {
            assert!(
                PROFILE.contains(&format!("(home-path \"/{relative}\")")),
                "{relative} is resolved for links but the profile does not deny it"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn a_linked_credential_folder_is_denied_where_it_really_is() {
        let directory = tempfile::tempdir().unwrap();
        let base = std::fs::canonicalize(directory.path()).unwrap();
        let home = base.join("home");
        let dotfiles = base.join("dotfiles/ssh");
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&dotfiles).unwrap();
        std::os::unix::fs::symlink(&dotfiles, home.join(".ssh")).unwrap();
        assert_eq!(resolved_credentials(&home), [dotfiles]);
    }

    #[test]
    fn package_managers_are_sent_to_the_cache() {
        let environment = cache_environment(Path::new("/c"));
        let named = |name: &str| {
            environment
                .iter()
                .find(|(variable, _)| *variable == name)
                .map(|(_, path)| path.clone())
        };
        assert_eq!(named("npm_config_cache"), Some(PathBuf::from("/c/npm")));
        assert_eq!(named("UV_CACHE_DIR"), Some(PathBuf::from("/c/uv")));
        assert!(environment.iter().all(|(_, path)| path.starts_with("/c")));
    }
}
