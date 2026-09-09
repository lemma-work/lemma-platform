//! Turning a release manifest into an installed runtime.
//!
//! One staging directory per attempt, promoted into place only once every
//! artifact has been downloaded, verified and expanded.

use super::*;

pub(crate) fn install_from_manifest(
    manifest_path: &Path,
    install_root: &Path,
    required_release: &str,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<InstalledRuntime> {
    stage_from_manifest(
        manifest_path,
        install_root,
        required_release,
        true,
        progress,
    )
}

pub(crate) fn reinstall_from_manifest(
    manifest_path: &Path,
    install_root: &Path,
    required_release: &str,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<InstalledRuntime> {
    stage_from_manifest(
        manifest_path,
        install_root,
        required_release,
        false,
        progress,
    )
}

pub(crate) fn stage_from_manifest(
    manifest_path: &Path,
    install_root: &Path,
    required_release: &str,
    reuse_existing: bool,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<InstalledRuntime> {
    let allow_local_artifacts = local_artifacts_enabled(manifest_path);
    let manifest = load_manifest_with_policy(manifest_path, allow_local_artifacts)?;
    if manifest.version != required_release {
        return Err(invalid(format!(
            "signed runtime release {} does not match desktop release {required_release}",
            manifest.version
        )));
    }
    let host = artifact_for(&manifest.host_packs, host_target(), "native host pack")?;
    let guest = artifact_for(
        &manifest.guest_runtimes,
        guest_target(),
        "managed guest runtime",
    )?;
    validate_artifact_target(
        host,
        host_platform(),
        host_architecture(),
        "native host pack",
    )?;
    validate_artifact_target(guest, "linux", host_architecture(), "managed guest runtime")?;
    let identity = artifact_identity(&manifest.version, host, guest);
    let releases = install_root.join("releases");
    let legacy = releases.join(&manifest.version);
    let legacy_runtime = installed_runtime(&legacy, &manifest.version);
    if reuse_existing
        && legacy_runtime.is_complete()
        && installed_artifacts_match(&legacy, &identity)
    {
        return Ok(legacy_runtime);
    }
    let identity_bytes = serde_json::to_vec(&identity).map_err(io::Error::other)?;
    let destination = releases.join(format!(
        "{}-{}",
        manifest.version,
        short_identity(&identity_bytes)
    ));
    let installed = installed_runtime(&destination, &manifest.version);
    if reuse_existing
        && installed.is_complete()
        && installed_artifacts_match(&destination, &identity)
    {
        return Ok(installed);
    }
    let destination = if destination.try_exists()? {
        releases.join(format!(
            "{}-{}-{}",
            destination.file_name().unwrap().to_string_lossy(),
            std::process::id(),
            unix_millis()?
        ))
    } else {
        destination
    };

    let download_total = host
        .size
        .checked_add(guest.size)
        .ok_or_else(|| invalid("combined artifact size overflow"))?;
    let expanded_total = host
        .expanded_size
        .checked_add(guest.expanded_size)
        .ok_or_else(|| invalid("combined expanded artifact size overflow"))?;
    let required_space = installation_space_required(download_total, expanded_total)?;
    preflight_free_space(install_root, required_space)?;
    let downloads = install_root.join("downloads").join(&manifest.version);
    fs::create_dir_all(&downloads)?;
    let client = download_client()?;
    let host_archive = download_artifact(
        &client,
        host,
        &downloads.join("host-pack.zip"),
        "Downloading application runtime",
        ProgressSpan {
            completed_before: 0,
            total: download_total,
        },
        manifest_path.parent().unwrap_or_else(|| Path::new(".")),
        allow_local_artifacts,
        progress,
    )?;
    let guest_archive = download_artifact(
        &client,
        guest,
        &downloads.join("guest-runtime.zip"),
        "Downloading private runtime",
        ProgressSpan {
            completed_before: host.size,
            total: download_total,
        },
        manifest_path.parent().unwrap_or_else(|| Path::new(".")),
        allow_local_artifacts,
        progress,
    )?;

    progress(InstallProgress {
        stage: "verify",
        component: "runtime",
        label: "Verifying and installing runtime",
        current: download_total,
        total: download_total,
        bytes: true,
    });
    let staging = install_root.join("releases").join(format!(
        ".{}-{}-{}.staging",
        manifest.version,
        std::process::id(),
        unix_millis()?
    ));
    fs::create_dir_all(&staging)?;
    let install_result: io::Result<()> = (|| {
        extract_archive(
            &host_archive,
            &staging,
            host.expanded_size,
            "host-extract",
            "host",
            "Installing application runtime",
            ProgressSpan {
                completed_before: 0,
                total: expanded_total,
            },
            progress,
        )?;
        extract_archive(
            &guest_archive,
            &staging.join("managed-runtime"),
            guest.expanded_size,
            "guest-extract",
            "guest",
            "Installing private runtime",
            ProgressSpan {
                completed_before: host.expanded_size,
                total: expanded_total,
            },
            progress,
        )?;
        progress(InstallProgress {
            stage: "validate",
            component: "runtime",
            label: "Validating installed runtime",
            current: 0,
            total: 1,
            bytes: false,
        });
        let staged = installed_runtime(&staging, &manifest.version);
        validate_installed(&staged)?;
        write_installed_artifacts(&staging, &identity)?;
        fs::create_dir_all(
            destination
                .parent()
                .ok_or_else(|| invalid("release destination has no parent"))?,
        )?;
        if destination.try_exists()? {
            return Err(invalid("candidate runtime destination already exists"));
        }
        fs::rename(&staging, &destination)?;
        sync_directory(
            destination
                .parent()
                .ok_or_else(|| invalid("release destination has no parent"))?,
        )?;
        Ok(())
    })();
    if install_result.is_err() {
        let _ = fs::remove_dir_all(&staging);
    }
    install_result?;
    let _ = fs::remove_file(host_archive);
    let _ = fs::remove_file(guest_archive);

    let installed = installed_runtime(&destination, &manifest.version);
    validate_installed(&installed)?;
    if !installed_artifacts_match(&destination, &identity) {
        return Err(invalid(
            "installed runtime artifact identity does not match the signed manifest",
        ));
    }
    Ok(installed)
}

/// A short, stable name for one exact set of artifacts.
///
/// The whole SHA-256 used to go in the directory name, and 64 hex characters
/// is 56 more than this needs. On Windows those 56 characters are the
/// difference between an installation that works and one that does not: the
/// deepest file in the host pack sits 193 characters below this directory, and
/// `MAX_PATH` is 260. Measured on a real installation, 1,349 files landed past
/// that limit -- written, because Rust addresses them as `\\?\`, and then
/// unreadable by everything that does not, including the pack's own Python.
/// The backend could not import a module that was sitting right there.
///
/// Eight hex characters is 2^32 of namespace for a directory that holds at
/// most a handful of releases, and a collision is not silent anyway: the
/// caller checks the recorded identity and, on a mismatch, installs beside it
/// under a suffixed name.
pub(crate) fn short_identity(identity_bytes: &[u8]) -> String {
    let digest = Sha256::digest(identity_bytes);
    hex::encode(&digest[..4])
}
