//! Installing a runtime release from its manifest.
//!
//! Was one 2,272-line file. Split along the install's own stages: read the
//! manifest, download an artifact, expand it, check the disk it is going on,
//! stage the release, and record what ended up installed.

use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use reqwest::blocking::Client;
use reqwest::header::{CONTENT_RANGE, RANGE};
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub(crate) const MANIFEST_SCHEMA_VERSION: u64 = 1;
pub(crate) const MAX_ARCHIVE_BYTES: u64 = 6 * 1024 * 1024 * 1024;
pub(crate) const MAX_EXTRACTED_BYTES: u128 = 12 * 1024 * 1024 * 1024;
pub(crate) const MAX_COMBINED_COMPRESSED_BYTES: u64 = 6 * 1024 * 1024 * 1024;
pub(crate) const MAX_COMBINED_EXPANDED_BYTES: u64 = 8 * 1024 * 1024 * 1024;
pub(crate) const MAX_ARCHIVE_ENTRIES: usize = 100_000;
pub(crate) const INSTALLED_ARTIFACTS_FILE: &str = ".lemma-runtime-artifacts.json";
pub(crate) const OPERATING_HEADROOM_BYTES: u64 = 4 * 1024 * 1024 * 1024;

mod disk_space;
mod download;
mod extract;
mod host_target;
mod installed;
mod manifest;
mod staging;

pub(crate) use disk_space::*;
pub(crate) use download::*;
pub(crate) use extract::*;
pub(crate) use host_target::*;
pub(crate) use installed::*;
pub(crate) use manifest::*;
pub(crate) use staging::*;

#[cfg(test)]
mod tests;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ArtifactRef {
    #[serde(default)]
    url: Option<String>,
    #[serde(default)]
    resource: Option<String>,
    sha256: String,
    size: u64,
    expanded_size: u64,
    format: String,
    platform: String,
    architecture: String,
    runtime_version: String,
}

#[derive(Debug, Deserialize)]
pub(crate) struct ReleaseManifest {
    schema_version: u64,
    version: String,
    /// How this manifest was produced, when whoever produced it wants to say.
    /// CI's desktop job stamps `ci-build-check`; see [`BUILD_CHECK_SOURCE`].
    #[serde(default)]
    artifact_source: Option<String>,
    #[serde(default)]
    host_packs: HashMap<String, ArtifactRef>,
    #[serde(default)]
    guest_runtimes: HashMap<String, ArtifactRef>,
}

/// The marker CI puts on the manifest it bakes into its build-check DMG.
///
/// That job exists to prove the app compiles and codesigns, so it stages
/// unresolvable URLs and zero digests rather than real artifacts. The DMG is
/// still signed and still launches, so without this it fails at first run with
/// a generic connection error and sends people looking for a firewall problem.
pub(crate) const BUILD_CHECK_SOURCE: &str = "ci-build-check";

#[derive(Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct InstalledArtifactIdentity {
    schema_version: u64,
    release: String,
    host_target: String,
    host_sha256: String,
    host_size: u64,
    guest_target: String,
    guest_sha256: String,
    guest_size: u64,
}

#[derive(Clone, Debug)]
pub struct InstalledRuntime {
    pub release: String,
    pub host_pack_root: PathBuf,
    pub managed_runtime_root: PathBuf,
}

#[derive(Clone, Debug)]
pub struct InstallProgress<'a> {
    pub stage: &'a str,
    pub component: &'a str,
    pub label: &'a str,
    pub current: u64,
    pub total: u64,
    pub bytes: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ProgressSpan {
    completed_before: u64,
    total: u64,
}

impl InstalledRuntime {
    pub fn is_complete(&self) -> bool {
        validate_installed(self).is_ok()
    }

    /// Returns true when this runtime was installed from a verified artifact
    /// manifest rather than merely copied into the release directory.
    ///
    /// The recorded identity is the durable trust handoff from installation to
    /// later launches from the local cache. Updates and explicit repairs still compare
    /// against their current manifest before installing anything new.
    pub fn has_recorded_artifact_identity(&self) -> bool {
        let Some(root) = self.host_pack_root.parent() else {
            return false;
        };
        if self.managed_runtime_root.parent() != Some(root) {
            return false;
        }
        let Some(identity) = read_installed_artifacts(root) else {
            return false;
        };
        identity.schema_version == MANIFEST_SCHEMA_VERSION
            && identity.release == self.release
            && identity.host_target == host_target()
            && identity.guest_target == guest_target()
            && valid_recorded_digest(&identity.host_sha256)
            && valid_recorded_digest(&identity.guest_sha256)
            && (1..=MAX_ARCHIVE_BYTES).contains(&identity.host_size)
            && (1..=MAX_ARCHIVE_BYTES).contains(&identity.guest_size)
    }
}

pub(crate) fn unix_millis() -> io::Result<u128> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .map_err(|error| io::Error::other(format!("system clock is before Unix epoch: {error}")))
}

pub(crate) fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}
