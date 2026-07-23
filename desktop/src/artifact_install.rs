use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use reqwest::blocking::Client;
use reqwest::header::{CONTENT_RANGE, RANGE};
use reqwest::redirect::Policy;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MANIFEST_SCHEMA_VERSION: u64 = 1;
const MAX_ARCHIVE_BYTES: u64 = 6 * 1024 * 1024 * 1024;
const MAX_EXTRACTED_BYTES: u128 = 12 * 1024 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES: usize = 100_000;
const INSTALLED_ARTIFACTS_FILE: &str = ".lemma-runtime-artifacts.json";

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactRef {
    url: String,
    sha256: String,
    size: u64,
    format: String,
}

#[derive(Debug, Deserialize)]
struct ReleaseManifest {
    schema_version: u64,
    version: String,
    #[serde(default)]
    host_packs: HashMap<String, ArtifactRef>,
    #[serde(default)]
    guest_runtimes: HashMap<String, ArtifactRef>,
}

#[derive(Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct InstalledArtifactIdentity {
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
    pub label: &'a str,
    pub downloaded: u64,
    pub total: u64,
}

#[derive(Clone, Copy, Debug)]
struct ProgressSpan {
    completed_before: u64,
    total: u64,
}

pub fn install_from_manifest(
    manifest_path: &Path,
    install_root: &Path,
    required_release: &str,
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
    let identity = artifact_identity(&manifest.version, host, guest);
    let destination = install_root.join("releases").join(&manifest.version);
    let installed = installed_runtime(&destination, &manifest.version);
    if installed.is_complete() && installed_artifacts_match(&destination, &identity) {
        return Ok(installed);
    }

    let total = host
        .size
        .checked_add(guest.size)
        .ok_or_else(|| invalid("combined artifact size overflow"))?;
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
            total,
        },
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
            total,
        },
        allow_local_artifacts,
        progress,
    )?;

    progress(InstallProgress {
        label: "Verifying and installing runtime",
        downloaded: total,
        total,
    });
    let staging = install_root.join("releases").join(format!(
        ".{}-{}-{}.staging",
        manifest.version,
        std::process::id(),
        unix_millis()?
    ));
    fs::create_dir_all(&staging)?;
    let install_result: io::Result<()> = (|| {
        extract_archive(&host_archive, &staging)?;
        extract_archive(&guest_archive, &staging.join("managed-runtime"))?;
        let staged = installed_runtime(&staging, &manifest.version);
        validate_installed(&staged)?;
        write_installed_artifacts(&staging, &identity)?;
        fs::create_dir_all(
            destination
                .parent()
                .ok_or_else(|| invalid("release destination has no parent"))?,
        )?;
        if destination.exists() {
            let quarantined = quarantine_path(&destination)?;
            fs::rename(&destination, quarantined)?;
        }
        fs::rename(&staging, &destination)?;
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

pub fn installed_runtime(root: &Path, release: &str) -> InstalledRuntime {
    InstalledRuntime {
        release: release.to_owned(),
        host_pack_root: root.join("local-runtime"),
        managed_runtime_root: root.join("managed-runtime"),
    }
}

pub fn manifest_release(path: &Path) -> io::Result<String> {
    Ok(load_manifest(path)?.version)
}

pub fn runtime_matches_manifest(
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

pub fn quarantine_runtime(runtime: &InstalledRuntime) -> io::Result<PathBuf> {
    validate_installed(runtime)?;
    let root = runtime
        .host_pack_root
        .parent()
        .ok_or_else(|| invalid("installed runtime has no release root"))?;
    if runtime.managed_runtime_root.parent() != Some(root) {
        return Err(invalid("installed runtime roots do not share one release"));
    }
    let quarantined = quarantine_path(root)?;
    fs::rename(root, &quarantined)?;
    Ok(quarantined)
}

impl InstalledRuntime {
    pub fn is_complete(&self) -> bool {
        validate_installed(self).is_ok()
    }
}

fn load_manifest(path: &Path) -> io::Result<ReleaseManifest> {
    load_manifest_with_policy(path, local_artifacts_enabled(path))
}

fn load_manifest_with_policy(
    path: &Path,
    allow_local_artifacts: bool,
) -> io::Result<ReleaseManifest> {
    let raw = fs::read(path)?;
    if raw.len() > 1024 * 1024 {
        return Err(invalid("release manifest exceeds 1 MiB"));
    }
    let manifest: ReleaseManifest = serde_json::from_slice(&raw).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid local release manifest: {error}"),
        )
    })?;
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION
        || manifest.version.is_empty()
        || manifest.version.len() > 128
        || !manifest
            .version
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
    {
        return Err(invalid("unsupported release manifest identity or schema"));
    }
    for artifact in manifest
        .host_packs
        .values()
        .chain(manifest.guest_runtimes.values())
    {
        validate_artifact(artifact, allow_local_artifacts)?;
    }
    Ok(manifest)
}

fn artifact_for<'a>(
    artifacts: &'a HashMap<String, ArtifactRef>,
    target: &str,
    label: &str,
) -> io::Result<&'a ArtifactRef> {
    artifacts.get(target).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!("release manifest has no {label} for {target}"),
        )
    })
}

fn validate_artifact(artifact: &ArtifactRef, allow_local_artifacts: bool) -> io::Result<()> {
    let url = reqwest::Url::parse(&artifact.url)
        .map_err(|error| invalid(format!("invalid artifact URL: {error}")))?;
    let safe_https = url.scheme() == "https";
    let safe_local = allow_local_artifacts
        && url.scheme() == "file"
        && url.host_str().is_none()
        && url.query().is_none()
        && url.to_file_path().is_ok();
    if (!safe_https && !safe_local)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || artifact.format != "zip"
        || artifact.size == 0
        || artifact.size > MAX_ARCHIVE_BYTES
        || artifact.sha256.len() != 64
        || !artifact
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(invalid("invalid or unsafe release artifact metadata"));
    }
    Ok(())
}

fn local_artifacts_enabled(manifest_path: &Path) -> bool {
    if std::env::var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS").as_deref() != Ok("1") {
        return false;
    }
    let Some(configured) = std::env::var_os("LEMMA_DESKTOP_RELEASE_MANIFEST") else {
        return false;
    };
    let configured = PathBuf::from(configured);
    configured.canonicalize().ok() == manifest_path.canonicalize().ok()
}

fn download_client() -> io::Result<Client> {
    Client::builder()
        .connect_timeout(std::time::Duration::from_secs(15))
        .timeout(std::time::Duration::from_secs(2 * 60 * 60))
        .redirect(Policy::custom(|attempt| {
            if attempt.previous().len() >= 5 {
                attempt.error("too many artifact redirects")
            } else if attempt.url().scheme() == "https" {
                attempt.follow()
            } else {
                attempt.error("artifact redirect was not HTTPS")
            }
        }))
        .build()
        .map_err(|error| io::Error::other(format!("artifact HTTP client failed: {error}")))
}

fn download_artifact(
    client: &Client,
    artifact: &ArtifactRef,
    destination: &Path,
    label: &str,
    progress_span: ProgressSpan,
    allow_local_artifacts: bool,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<PathBuf> {
    validate_artifact(artifact, allow_local_artifacts)?;
    let url = reqwest::Url::parse(&artifact.url)
        .map_err(|error| invalid(format!("invalid artifact URL: {error}")))?;
    if url.scheme() == "file" {
        return copy_local_artifact(&url, artifact, destination, label, progress_span, progress);
    }
    if archive_matches(destination, artifact)? {
        progress(InstallProgress {
            label,
            downloaded: progress_span.completed_before + artifact.size,
            total: progress_span.total,
        });
        return Ok(destination.to_owned());
    }
    let partial = destination.with_extension("zip.part");
    if archive_matches(&partial, artifact)? {
        replace_archive(&partial, destination)?;
        progress(InstallProgress {
            label,
            downloaded: progress_span.completed_before + artifact.size,
            total: progress_span.total,
        });
        return Ok(destination.to_owned());
    }
    let mut offset = partial
        .metadata()
        .map(|metadata| metadata.len())
        .unwrap_or(0);
    if offset >= artifact.size {
        fs::remove_file(&partial)?;
        offset = 0;
    }
    let mut request = client.get(&artifact.url);
    if offset > 0 {
        request = request.header(RANGE, format!("bytes={offset}-"));
    }
    let mut response = request
        .send()
        .map_err(|error| io::Error::other(download_error(&error)))?;
    if !response.status().is_success() {
        return Err(io::Error::other(format!(
            "artifact download failed with HTTP {}",
            response.status().as_u16()
        )));
    }
    let resumed = offset > 0 && response.status() == reqwest::StatusCode::PARTIAL_CONTENT;
    if resumed {
        let content_range = response
            .headers()
            .get(CONTENT_RANGE)
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default();
        if !valid_content_range(content_range, offset, artifact.size) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "artifact server returned an invalid resume range",
            ));
        }
    }
    if offset > 0 && !resumed {
        offset = 0;
    }
    let mut options = OpenOptions::new();
    options.create(true).write(true);
    if resumed {
        options.append(true);
    } else {
        options.truncate(true);
    }
    let mut file = options.open(&partial)?;
    let mut downloaded = offset;
    let mut last_reported = offset;
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let count = response.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        downloaded = downloaded
            .checked_add(count as u64)
            .ok_or_else(|| invalid("artifact byte count overflow"))?;
        if downloaded > artifact.size {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "artifact download exceeded the signed size",
            ));
        }
        file.write_all(&buffer[..count])?;
        if downloaded == artifact.size
            || downloaded.saturating_sub(last_reported) >= 4 * 1024 * 1024
        {
            progress(InstallProgress {
                label,
                downloaded: progress_span.completed_before + downloaded,
                total: progress_span.total,
            });
            last_reported = downloaded;
        }
    }
    file.sync_all()?;
    if downloaded < artifact.size {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "artifact download ended early and can be resumed",
        ));
    }
    if !archive_matches(&partial, artifact)? {
        let _ = fs::remove_file(&partial);
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "artifact size or SHA-256 did not match the signed manifest",
        ));
    }
    replace_archive(&partial, destination)?;
    Ok(destination.to_owned())
}

fn copy_local_artifact(
    url: &reqwest::Url,
    artifact: &ArtifactRef,
    destination: &Path,
    label: &str,
    progress_span: ProgressSpan,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<PathBuf> {
    let source = url
        .to_file_path()
        .map_err(|_| invalid("local artifact URL is not an absolute file path"))?
        .canonicalize()
        .map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("local artifact is unavailable: {error}"),
            )
        })?;
    let metadata = source.metadata()?;
    if !metadata.is_file() || metadata.len() != artifact.size {
        return Err(invalid(
            "local artifact size did not match the test manifest",
        ));
    }
    if archive_matches(destination, artifact)? {
        progress(InstallProgress {
            label,
            downloaded: progress_span.completed_before + artifact.size,
            total: progress_span.total,
        });
        return Ok(destination.to_owned());
    }
    let partial = destination.with_extension("zip.part");
    let _ = fs::remove_file(&partial);
    let mut input = File::open(source)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    let mut output = options.open(&partial)?;
    let mut downloaded = 0_u64;
    let mut last_reported = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let count = input.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        downloaded = downloaded
            .checked_add(count as u64)
            .ok_or_else(|| invalid("local artifact byte count overflow"))?;
        if downloaded > artifact.size {
            return Err(invalid(
                "local artifact exceeded the size in the test manifest",
            ));
        }
        output.write_all(&buffer[..count])?;
        if downloaded == artifact.size
            || downloaded.saturating_sub(last_reported) >= 4 * 1024 * 1024
        {
            progress(InstallProgress {
                label,
                downloaded: progress_span.completed_before + downloaded,
                total: progress_span.total,
            });
            last_reported = downloaded;
        }
    }
    output.sync_all()?;
    if !archive_matches(&partial, artifact)? {
        let _ = fs::remove_file(&partial);
        return Err(invalid(
            "local artifact SHA-256 did not match the test manifest",
        ));
    }
    replace_archive(&partial, destination)?;
    Ok(destination.to_owned())
}

fn replace_archive(source: &Path, destination: &Path) -> io::Result<()> {
    if destination.exists() {
        fs::rename(destination, quarantine_path(destination)?)?;
    }
    fs::rename(source, destination)
}

fn quarantine_path(path: &Path) -> io::Result<PathBuf> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| invalid("artifact path has no safe file name"))?;
    Ok(path.with_file_name(format!(".{name}.invalid-{}", unix_millis()?)))
}

fn valid_content_range(value: &str, expected_start: u64, expected_size: u64) -> bool {
    let Some(range) = value.strip_prefix("bytes ") else {
        return false;
    };
    let Some((bounds, total)) = range.split_once('/') else {
        return false;
    };
    let Some((start, end)) = bounds.split_once('-') else {
        return false;
    };
    let (Ok(start), Ok(end), Ok(total)) = (
        start.parse::<u64>(),
        end.parse::<u64>(),
        total.parse::<u64>(),
    ) else {
        return false;
    };
    start == expected_start && start <= end && end < expected_size && total == expected_size
}

fn archive_matches(path: &Path, artifact: &ArtifactRef) -> io::Result<bool> {
    let Ok(metadata) = path.metadata() else {
        return Ok(false);
    };
    if !metadata.is_file() || metadata.len() != artifact.size {
        return Ok(false);
    }
    Ok(file_sha256(path)? == artifact.sha256)
}

fn file_sha256(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn extract_archive(path: &Path, destination: &Path) -> io::Result<()> {
    fs::create_dir_all(destination)?;
    let mut archive = zip::ZipArchive::new(File::open(path)?)
        .map_err(|error| invalid(format!("invalid ZIP archive: {error}")))?;
    if archive.len() > MAX_ARCHIVE_ENTRIES
        || archive.decompressed_size().unwrap_or(u128::MAX) > MAX_EXTRACTED_BYTES
        || archive
            .has_overlapping_files()
            .map_err(|error| invalid(format!("invalid ZIP layout: {error}")))?
    {
        return Err(invalid("ZIP archive exceeds safe extraction limits"));
    }
    let mut seen = HashSet::new();
    let mut extracted = 0_u128;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| invalid(format!("invalid ZIP entry: {error}")))?;
        let relative = entry
            .enclosed_name()
            .ok_or_else(|| invalid("ZIP entry escapes the installation directory"))?;
        if relative.as_os_str().is_empty() || !seen.insert(relative.clone()) {
            return Err(invalid("ZIP archive contains an empty or duplicate path"));
        }
        if entry
            .unix_mode()
            .is_some_and(|mode| mode & 0o170000 == 0o120000)
        {
            return Err(invalid("ZIP archive contains a symbolic link"));
        }
        extracted = extracted
            .checked_add(u128::from(entry.size()))
            .ok_or_else(|| invalid("ZIP extracted-size overflow"))?;
        if extracted > MAX_EXTRACTED_BYTES {
            return Err(invalid("ZIP archive exceeds safe extraction limits"));
        }
        let output = destination.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&output)?;
            continue;
        }
        if let Some(parent) = output.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(entry.unix_mode().unwrap_or(0o600) & 0o777);
        }
        let mut output_file = options.open(&output)?;
        let copied = io::copy(&mut entry, &mut output_file)?;
        if copied != entry.size() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "ZIP entry size changed during extraction",
            ));
        }
        output_file.sync_all()?;
    }
    Ok(())
}

fn validate_installed(runtime: &InstalledRuntime) -> io::Result<()> {
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

fn artifact_identity(
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

fn installed_artifacts_match(root: &Path, expected: &InstalledArtifactIdentity) -> bool {
    fs::read(root.join(INSTALLED_ARTIFACTS_FILE))
        .ok()
        .and_then(|raw| serde_json::from_slice::<InstalledArtifactIdentity>(&raw).ok())
        .is_some_and(|actual| actual == *expected)
}

fn write_installed_artifacts(root: &Path, identity: &InstalledArtifactIdentity) -> io::Result<()> {
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

fn download_error(error: &reqwest::Error) -> &'static str {
    if error.is_timeout() {
        "artifact download timed out"
    } else if error.is_connect() {
        "could not connect to the artifact host"
    } else {
        "artifact download failed"
    }
}

fn unix_millis() -> io::Result<u128> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .map_err(|error| io::Error::other(format!("system clock is before Unix epoch: {error}")))
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
fn host_target() -> &'static str {
    "aarch64-apple-darwin"
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
fn guest_target() -> &'static str {
    "macos-aarch64"
}

#[cfg(all(windows, target_arch = "x86_64"))]
fn host_target() -> &'static str {
    "x86_64-pc-windows-msvc"
}

#[cfg(all(windows, target_arch = "x86_64"))]
fn guest_target() -> &'static str {
    "windows-x86_64"
}

#[cfg(not(any(
    all(target_os = "macos", target_arch = "aarch64"),
    all(windows, target_arch = "x86_64")
)))]
fn host_target() -> &'static str {
    "unsupported"
}

#[cfg(not(any(
    all(target_os = "macos", target_arch = "aarch64"),
    all(windows, target_arch = "x86_64")
)))]
fn guest_target() -> &'static str {
    "unsupported"
}

#[cfg(test)]
mod tests {
    use super::*;
    use zip::write::SimpleFileOptions;

    fn write_zip(path: &Path, entries: &[(&str, &[u8], u32)]) {
        let mut writer = zip::ZipWriter::new(File::create(path).unwrap());
        for (name, body, mode) in entries {
            writer
                .start_file(
                    *name,
                    SimpleFileOptions::default()
                        .compression_method(zip::CompressionMethod::Deflated)
                        .unix_permissions(*mode),
                )
                .unwrap();
            writer.write_all(body).unwrap();
        }
        writer.finish().unwrap();
    }

    fn complete_runtime(root: &Path, release: &str) -> InstalledRuntime {
        let runtime = installed_runtime(root, release);
        fs::create_dir_all(&runtime.host_pack_root).unwrap();
        fs::write(
            runtime.host_pack_root.join("release.json"),
            serde_json::to_vec(&serde_json::json!({"version": release})).unwrap(),
        )
        .unwrap();
        let host_files: &[&str] = if cfg!(windows) {
            &[
                "pack.json",
                "backend/python/python.exe",
                "frontend/node/node.exe",
            ]
        } else {
            &[
                "pack.json",
                "backend/python/bin/python3",
                "frontend/node/bin/node",
            ]
        };
        for relative in host_files {
            let path = runtime.host_pack_root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, b"present").unwrap();
        }
        fs::write(
            runtime.host_pack_root.join("pack.json"),
            serde_json::to_vec(&serde_json::json!({"release": release})).unwrap(),
        )
        .unwrap();
        let guest = runtime.managed_runtime_root.join(guest_target());
        fs::create_dir_all(&guest).unwrap();
        fs::write(
            guest.join("runtime.json"),
            serde_json::to_vec(&serde_json::json!({"target": guest_target()})).unwrap(),
        )
        .unwrap();
        for relative in if cfg!(target_os = "macos") {
            &["vmlinuz", "initrd", "disk.raw"][..]
        } else {
            &["rootfs.tar"][..]
        } {
            fs::write(guest.join(relative), b"present").unwrap();
        }
        runtime
    }

    #[test]
    fn extracts_only_enclosed_regular_files_and_preserves_executable_mode() {
        let root = tempfile::tempdir().unwrap();
        let archive = root.path().join("runtime.zip");
        write_zip(&archive, &[("safe/bin/run", b"runtime", 0o755)]);

        extract_archive(&archive, &root.path().join("output")).unwrap();

        let output = root.path().join("output/safe/bin/run");
        assert_eq!(fs::read(&output).unwrap(), b"runtime");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                output.metadata().unwrap().permissions().mode() & 0o111,
                0o111
            );
        }
    }

    #[test]
    fn rejects_zip_traversal_and_symbolic_links() {
        let root = tempfile::tempdir().unwrap();
        let traversal = root.path().join("traversal.zip");
        write_zip(&traversal, &[("../escape", b"bad", 0o600)]);
        assert!(extract_archive(&traversal, &root.path().join("traversal-out")).is_err());
        assert!(!root.path().join("escape").exists());

        let symlink = root.path().join("symlink.zip");
        let mut writer = zip::ZipWriter::new(File::create(&symlink).unwrap());
        writer
            .add_symlink(
                "link",
                "target",
                SimpleFileOptions::default().unix_permissions(0o777),
            )
            .unwrap();
        writer.finish().unwrap();
        assert!(extract_archive(&symlink, &root.path().join("symlink-out")).is_err());
    }

    #[test]
    fn manifest_requires_https_digest_size_format_and_safe_release_name() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("lemma-local.json");
        let artifact = serde_json::json!({
            "url": "https://downloads.example.test/runtime.zip",
            "sha256": "a".repeat(64),
            "size": 42,
            "format": "zip",
        });
        fs::write(
            &path,
            serde_json::to_vec(&serde_json::json!({
                "schema_version": 1,
                "version": "1.2.3",
                "host_packs": {host_target(): artifact.clone()},
                "guest_runtimes": {guest_target(): artifact},
            }))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(load_manifest(&path).unwrap().version, "1.2.3");
        assert!(
            install_from_manifest(&path, &root.path().join("install"), "9.9.9", &mut |_| {})
                .is_err()
        );

        let unsafe_manifest = fs::read_to_string(&path)
            .unwrap()
            .replace("1.2.3", "../escape");
        fs::write(&path, unsafe_manifest).unwrap();
        assert!(load_manifest(&path).is_err());
    }

    #[test]
    fn local_file_artifacts_are_explicitly_gated_and_still_digest_verified() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("runtime.zip");
        fs::write(&source, b"locally-built-runtime").unwrap();
        let artifact = ArtifactRef {
            url: reqwest::Url::from_file_path(&source).unwrap().to_string(),
            sha256: file_sha256(&source).unwrap(),
            size: source.metadata().unwrap().len(),
            format: "zip".into(),
        };

        assert!(validate_artifact(&artifact, false).is_err());
        validate_artifact(&artifact, true).unwrap();

        let destination = root.path().join("downloads/runtime.zip");
        fs::create_dir_all(destination.parent().unwrap()).unwrap();
        let mut reports = Vec::new();
        let copied = download_artifact(
            &download_client().unwrap(),
            &artifact,
            &destination,
            "Local runtime",
            ProgressSpan {
                completed_before: 0,
                total: artifact.size,
            },
            true,
            &mut |progress| reports.push((progress.downloaded, progress.total)),
        )
        .unwrap();

        assert_eq!(fs::read(copied).unwrap(), b"locally-built-runtime");
        assert_eq!(reports.last(), Some(&(artifact.size, artifact.size)));

        let changed = ArtifactRef {
            sha256: "0".repeat(64),
            ..artifact
        };
        assert!(download_artifact(
            &download_client().unwrap(),
            &changed,
            &root.path().join("changed.zip"),
            "Local runtime",
            ProgressSpan {
                completed_before: 0,
                total: changed.size,
            },
            true,
            &mut |_| {},
        )
        .is_err());
    }

    #[test]
    fn installed_runtime_requires_every_native_and_guest_marker() {
        let root = tempfile::tempdir().unwrap();
        let runtime = installed_runtime(root.path(), "1.2.3");
        fs::create_dir_all(&runtime.host_pack_root).unwrap();
        fs::write(
            runtime.host_pack_root.join("release.json"),
            br#"{"version":"1.2.3"}"#,
        )
        .unwrap();
        assert!(!runtime.is_complete());
        assert!(complete_runtime(root.path(), "1.2.3").is_complete());
    }

    #[test]
    fn cached_runtime_must_match_the_signed_artifact_identity() {
        let root = tempfile::tempdir().unwrap();
        let release = root.path().join("1.2.3");
        let runtime = complete_runtime(&release, "1.2.3");
        let host = ArtifactRef {
            url: "https://downloads.example.test/host.zip".into(),
            sha256: "a".repeat(64),
            size: 42,
            format: "zip".into(),
        };
        let guest = ArtifactRef {
            url: "https://downloads.example.test/guest.zip".into(),
            sha256: "b".repeat(64),
            size: 84,
            format: "zip".into(),
        };
        let expected = artifact_identity("1.2.3", &host, &guest);

        assert!(runtime.is_complete());
        assert!(!installed_artifacts_match(&release, &expected));
        write_installed_artifacts(&release, &expected).unwrap();
        assert!(installed_artifacts_match(&release, &expected));

        let changed = InstalledArtifactIdentity {
            guest_sha256: "c".repeat(64),
            ..expected
        };
        assert!(!installed_artifacts_match(&release, &changed));
    }

    #[test]
    fn quarantine_moves_only_one_verified_immutable_release() {
        let root = tempfile::tempdir().unwrap();
        let release = root.path().join("1.2.3");
        let runtime = complete_runtime(&release, "1.2.3");

        let quarantined = quarantine_runtime(&runtime).unwrap();

        assert!(!release.exists());
        assert!(quarantined.is_dir());
        assert!(quarantined
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with(".1.2.3.invalid-"));
    }

    #[test]
    fn accepts_only_exact_resume_content_ranges() {
        assert!(valid_content_range("bytes 12-99/100", 12, 100));
        assert!(!valid_content_range("bytes 11-99/100", 12, 100));
        assert!(!valid_content_range("bytes 12-100/100", 12, 100));
        assert!(!valid_content_range("bytes 12-99/*", 12, 100));
        assert!(!valid_content_range("items 12-99/100", 12, 100));
    }
}
