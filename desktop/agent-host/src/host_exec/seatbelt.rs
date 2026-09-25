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
    pub tmp: PathBuf,
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
        for (index, grant) in self.grants.iter().take(MAX_GRANTS).enumerate() {
            define(&format!("GRANT_{index}"), grant);
        }
        arguments
    }
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
    }
}
