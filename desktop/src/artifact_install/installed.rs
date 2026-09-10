//! What this machine already has: which release is installed, whether it
//! still matches its manifest, and which older ones nothing points at.

use super::*;

/// Delete release directories nothing points at any more.
///
/// Installing never removes anything, deliberately: a runtime that is still
/// serving has to survive its own replacement, which is why activation records
/// two -- `installedRuntime` and `previousRuntime`. Nothing removed the third.
/// Every upgrade left another expanded release behind, about 2.2 GB each, for
/// the life of the installation; four of them and a machine is carrying three
/// runtimes it can never use again. On Windows the earlier 64-character
/// release names make those leftovers unreadable as well as useless.
///
/// Deliberately timid about what it will touch. A direct child of `releases/`;
/// not hidden, because an interrupted install leaves `.<version>-<pid>.staging`
/// that another process may still be writing; recognisably one of ours, so a
/// directory somebody put here by hand is left alone; and never one the caller
/// named. A removal that fails is left for next time rather than reported --
/// on Windows that is what a release still in use does, and it is the right
/// outcome.
pub(crate) fn prune_retired_releases(install_root: &Path, keep: &[PathBuf]) -> Vec<PathBuf> {
    fn resolved(path: &Path) -> PathBuf {
        fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
    }

    let kept: Vec<PathBuf> = keep.iter().map(|path| resolved(path)).collect();
    let Ok(entries) = fs::read_dir(install_root.join("releases")) else {
        return Vec::new();
    };
    let mut removed = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if !entry.file_type().is_ok_and(|kind| kind.is_dir()) {
            continue;
        }
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        if name.starts_with('.') {
            continue;
        }
        if !path.join("local-runtime").is_dir() || !path.join("managed-runtime").is_dir() {
            continue;
        }
        if kept.contains(&resolved(&path)) {
            continue;
        }
        if fs::remove_dir_all(&path).is_ok() {
            removed.push(path);
        }
    }
    removed
}

/// How long an unfinished install may sit before it is treated as abandoned.
///
/// Generous by a wide margin: an install is minutes of work, and this is a day.
/// The cost of being wrong in one direction is deleting a directory somebody is
/// still writing to; in the other, it is leaving 2.2 GB on the disk until the
/// next attempt. Only one of those is recoverable by waiting.
const ABANDONED_STAGING: Duration = Duration::from_secs(24 * 60 * 60);

/// Remove staging directories no install could still be using.
///
/// `prune_retired_releases` cannot: it skips hidden entries on purpose,
/// precisely because a `.staging` may be a live install in progress. So
/// nothing removed these. A failed install cleans up after itself, but a
/// *killed* one cannot -- a crash, a power cut, quitting mid-install -- and
/// each one it leaves is a fully expanded runtime, about 2.2 GB, hidden, for
/// the life of the installation.
///
/// Decided on the timestamp the name already carries, not on the directory's
/// mtime: extracting into it keeps the mtime fresh, so an install that died
/// hours in would look like it had just started. A name that does not parse is
/// not one of ours and is left alone.
pub(crate) fn prune_abandoned_staging(install_root: &Path, now_millis: u128) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(install_root.join("releases")) else {
        return Vec::new();
    };
    let mut removed = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if !entry.file_type().is_ok_and(|kind| kind.is_dir()) {
            continue;
        }
        let Some(started) = staging_started_at(&path) else {
            continue;
        };
        // `saturating_sub`, because a clock that went backwards should leave
        // the directory alone rather than make everything look ancient.
        if now_millis.saturating_sub(started) < ABANDONED_STAGING.as_millis() {
            continue;
        }
        if fs::remove_dir_all(&path).is_ok() {
            removed.push(path);
        }
    }
    removed
}

/// When a staging directory was created, from its own name.
///
/// `.<version>-<pid>-<millis>.staging`, read from the right because a version
/// may contain a hyphen of its own.
fn staging_started_at(path: &Path) -> Option<u128> {
    let name = path.file_name()?.to_str()?;
    let body = name.strip_prefix('.')?.strip_suffix(".staging")?;
    let (rest, millis) = body.rsplit_once('-')?;
    let (version, pid) = rest.rsplit_once('-')?;
    if version.is_empty() || pid.parse::<u32>().is_err() {
        return None;
    }
    millis.parse().ok()
}

pub(crate) fn installed_runtime(root: &Path, release: &str) -> InstalledRuntime {
    InstalledRuntime {
        release: release.to_owned(),
        host_pack_root: root.join("local-runtime"),
        managed_runtime_root: root.join("managed-runtime"),
    }
}

pub(crate) fn manifest_release(path: &Path) -> io::Result<String> {
    Ok(load_manifest(path)?.version)
}

pub(crate) fn runtime_matches_manifest(
    runtime: &InstalledRuntime,
    manifest_path: &Path,
    required_release: &str,
) -> io::Result<bool> {
    let manifest = load_manifest(manifest_path)?;
    if manifest.version != required_release || runtime.release != required_release {
        return Ok(false);
    }
    let host = artifact_for(&manifest.host_packs, host_target(), "native host pack")?;
    let guest = artifact_for(
        &manifest.guest_runtimes,
        guest_target(),
        "managed guest runtime",
    )?;
    let root = runtime
        .host_pack_root
        .parent()
        .ok_or_else(|| invalid("installed runtime has no release root"))?;
    Ok(runtime.is_complete()
        && installed_artifacts_match(root, &artifact_identity(required_release, host, guest)))
}

pub(crate) fn validate_installed(runtime: &InstalledRuntime) -> io::Result<()> {
    let release: serde_json::Value =
        serde_json::from_slice(&fs::read(runtime.host_pack_root.join("release.json"))?)
            .map_err(|error| invalid(format!("invalid installed release marker: {error}")))?;
    if release["version"].as_str() != Some(&runtime.release) {
        return Err(invalid("installed host pack release does not match"));
    }
    let pack: serde_json::Value =
        serde_json::from_slice(&fs::read(runtime.host_pack_root.join("pack.json"))?)
            .map_err(|error| invalid(format!("invalid installed host pack marker: {error}")))?;
    if pack["release"].as_str() != Some(&runtime.release) {
        return Err(invalid("installed host pack marker does not match release"));
    }
    let host_files: &[&str] = if cfg!(windows) {
        &["backend/python/python.exe", "frontend/node/node.exe"]
    } else {
        &["backend/python/bin/python3", "frontend/node/bin/node"]
    };
    if host_files.iter().any(|path| {
        runtime
            .host_pack_root
            .join(path)
            .metadata()
            .map_or(true, |metadata| !metadata.is_file() || metadata.len() == 0)
    }) {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "installed native host pack is incomplete",
        ));
    }
    let managed_marker = runtime
        .managed_runtime_root
        .join(guest_target())
        .join("runtime.json");
    if !managed_marker.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "installed managed runtime marker is missing",
        ));
    }
    let managed: serde_json::Value = serde_json::from_slice(&fs::read(&managed_marker)?)
        .map_err(|error| invalid(format!("invalid managed runtime marker: {error}")))?;
    if managed["target"].as_str() != Some(guest_target()) {
        return Err(invalid("managed runtime marker target does not match"));
    }
    let guest_files: &[&str] = if cfg!(target_os = "macos") {
        &["vmlinuz", "initrd", "disk.raw"]
    } else {
        &["rootfs.tar"]
    };
    let guest_root = runtime.managed_runtime_root.join(guest_target());
    if guest_files.iter().any(|path| {
        guest_root
            .join(path)
            .metadata()
            .map_or(true, |metadata| !metadata.is_file() || metadata.len() == 0)
    }) {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "installed managed guest runtime is incomplete",
        ));
    }
    Ok(())
}

pub(crate) fn artifact_identity(
    release: &str,
    host: &ArtifactRef,
    guest: &ArtifactRef,
) -> InstalledArtifactIdentity {
    InstalledArtifactIdentity {
        schema_version: MANIFEST_SCHEMA_VERSION,
        release: release.to_owned(),
        host_target: host_target().to_owned(),
        host_sha256: host.sha256.clone(),
        host_size: host.size,
        guest_target: guest_target().to_owned(),
        guest_sha256: guest.sha256.clone(),
        guest_size: guest.size,
    }
}

pub(crate) fn installed_artifacts_match(root: &Path, expected: &InstalledArtifactIdentity) -> bool {
    read_installed_artifacts(root).is_some_and(|actual| actual == *expected)
}

pub(crate) fn read_installed_artifacts(root: &Path) -> Option<InstalledArtifactIdentity> {
    fs::read(root.join(INSTALLED_ARTIFACTS_FILE))
        .ok()
        .and_then(|raw| serde_json::from_slice::<InstalledArtifactIdentity>(&raw).ok())
}

pub(crate) fn valid_recorded_digest(digest: &str) -> bool {
    digest.len() == 64
        && digest.bytes().any(|byte| byte != b'0')
        && digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(crate) fn write_installed_artifacts(
    root: &Path,
    identity: &InstalledArtifactIdentity,
) -> io::Result<()> {
    let path = root.join(INSTALLED_ARTIFACTS_FILE);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(&serde_json::to_vec(identity)?)?;
    file.write_all(b"\n")?;
    file.sync_all()
}
