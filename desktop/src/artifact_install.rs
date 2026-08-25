use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
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
const MAX_COMBINED_COMPRESSED_BYTES: u64 = 750 * 1024 * 1024;
const MAX_COMBINED_EXPANDED_BYTES: u64 = 2250 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES: usize = 100_000;
const INSTALLED_ARTIFACTS_FILE: &str = ".lemma-runtime-artifacts.json";
const OPERATING_HEADROOM_BYTES: u64 = 4 * 1024 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ArtifactRef {
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
struct ReleaseManifest {
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
const BUILD_CHECK_SOURCE: &str = "ci-build-check";

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
    pub stage: &'a str,
    pub component: &'a str,
    pub label: &'a str,
    pub current: u64,
    pub total: u64,
    pub bytes: bool,
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
    validate_artifact_target(
        host,
        host_platform(),
        host_architecture(),
        "native host pack",
    )?;
    validate_artifact_target(guest, "linux", host_architecture(), "managed guest runtime")?;
    let identity = artifact_identity(&manifest.version, host, guest);
    let destination = install_root.join("releases").join(&manifest.version);
    let installed = installed_runtime(&destination, &manifest.version);
    if installed.is_complete() && installed_artifacts_match(&destination, &identity) {
        return Ok(installed);
    }

    let download_total = host
        .size
        .checked_add(guest.size)
        .ok_or_else(|| invalid("combined artifact size overflow"))?;
    let expanded_total = host
        .expanded_size
        .checked_add(guest.expanded_size)
        .ok_or_else(|| invalid("combined expanded artifact size overflow"))?;
    if download_total > MAX_COMBINED_COMPRESSED_BYTES {
        return Err(invalid(
            "combined runtime archives exceed the 750 MiB product limit",
        ));
    }
    if expanded_total > MAX_COMBINED_EXPANDED_BYTES {
        return Err(invalid(
            "expanded immutable runtime exceeds the 2.25 GiB product limit",
        ));
    }
    preflight_free_space(install_root, expanded_total)?;
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
        if destination.exists() {
            let quarantined = quarantine_path(&destination)?;
            fs::rename(&destination, quarantined)?;
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
    // Checked before anything is downloaded, and on the marker rather than on
    // the placeholder hostname: the unresolvable URL is an implementation
    // detail of the stub, the marker is the statement of intent.
    if manifest.artifact_source.as_deref() == Some(BUILD_CHECK_SOURCE) {
        return Err(invalid(
            "this app came from a CI build-check DMG, which carries no runtime to \
             install. Build an installable one with the Release Local Images \
             workflow (publish: false) - see desktop/README.md",
        ));
    }
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
        if artifact.runtime_version != manifest.version {
            return Err(invalid(
                "artifact runtime version does not match the release manifest",
            ));
        }
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
    let safe_source = match (&artifact.url, &artifact.resource) {
        (Some(value), None) => {
            let url = reqwest::Url::parse(value)
                .map_err(|error| invalid(format!("invalid artifact URL: {error}")))?;
            let safe_https = url.scheme() == "https";
            let safe_local = allow_local_artifacts
                && url.scheme() == "file"
                && url.host_str().is_none()
                && url.query().is_none()
                && url.to_file_path().is_ok();
            (safe_https || safe_local)
                && url.username().is_empty()
                && url.password().is_none()
                && url.fragment().is_none()
        }
        (None, Some(resource)) => {
            !resource.is_empty()
                && resource.len() <= 128
                && !resource.contains(['/', '\\'])
                && resource
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        }
        _ => false,
    };
    if !safe_source
        || artifact.format != "zip"
        || artifact.size == 0
        || artifact.size > MAX_ARCHIVE_BYTES
        || artifact.expanded_size == 0
        || u128::from(artifact.expanded_size) > MAX_EXTRACTED_BYTES
        || !valid_metadata_name(&artifact.platform)
        || !valid_metadata_name(&artifact.architecture)
        || artifact.runtime_version.is_empty()
        || artifact.runtime_version.len() > 128
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

fn valid_metadata_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn validate_artifact_target(
    artifact: &ArtifactRef,
    platform: &str,
    architecture: &str,
    label: &str,
) -> io::Result<()> {
    if artifact.platform != platform || artifact.architecture != architecture {
        return Err(invalid(format!(
            "{label} metadata targets {}-{}, expected {platform}-{architecture}",
            artifact.platform, artifact.architecture
        )));
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

#[allow(clippy::too_many_arguments)]
fn download_artifact(
    client: &Client,
    artifact: &ArtifactRef,
    destination: &Path,
    label: &str,
    progress_span: ProgressSpan,
    resource_root: &Path,
    allow_local_artifacts: bool,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<PathBuf> {
    validate_artifact(artifact, allow_local_artifacts)?;
    if let Some(resource) = artifact.resource.as_deref() {
        return copy_artifact_file(
            &resource_root.join(resource),
            artifact,
            destination,
            label,
            progress_span,
            progress,
        );
    }
    let url = reqwest::Url::parse(
        artifact
            .url
            .as_deref()
            .ok_or_else(|| invalid("artifact has no download URL"))?,
    )
    .map_err(|error| invalid(format!("invalid artifact URL: {error}")))?;
    if url.scheme() == "file" {
        return copy_local_artifact(&url, artifact, destination, label, progress_span, progress);
    }
    if archive_matches(destination, artifact)? {
        progress(InstallProgress {
            stage: "download",
            component: if label.contains("private") {
                "guest"
            } else {
                "host"
            },
            label,
            current: progress_span.completed_before + artifact.size,
            total: progress_span.total,
            bytes: true,
        });
        return Ok(destination.to_owned());
    }
    let partial = destination.with_extension("zip.part");
    if archive_matches(&partial, artifact)? {
        replace_archive(&partial, destination)?;
        progress(InstallProgress {
            stage: "download",
            component: if label.contains("private") {
                "guest"
            } else {
                "host"
            },
            label,
            current: progress_span.completed_before + artifact.size,
            total: progress_span.total,
            bytes: true,
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
    let mut request = client.get(url);
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
    let mut digest = Sha256::new();
    if resumed {
        hash_prefix(&partial, offset, &mut digest)?;
    }
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
        digest.update(&buffer[..count]);
        if downloaded == artifact.size
            || downloaded.saturating_sub(last_reported) >= 4 * 1024 * 1024
        {
            progress(InstallProgress {
                stage: "download",
                component: if label.contains("private") {
                    "guest"
                } else {
                    "host"
                },
                label,
                current: progress_span.completed_before + downloaded,
                total: progress_span.total,
                bytes: true,
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
    if downloaded != artifact.size || format!("{:x}", digest.finalize()) != artifact.sha256 {
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
    copy_artifact_file(
        &source,
        artifact,
        destination,
        label,
        progress_span,
        progress,
    )
}

fn copy_artifact_file(
    source: &Path,
    artifact: &ArtifactRef,
    destination: &Path,
    label: &str,
    progress_span: ProgressSpan,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<PathBuf> {
    let metadata = source.metadata()?;
    if !metadata.is_file() || metadata.len() != artifact.size {
        return Err(invalid(
            "local artifact size did not match the test manifest",
        ));
    }
    if archive_matches(destination, artifact)? {
        progress(InstallProgress {
            stage: "download",
            component: if label.contains("private") {
                "guest"
            } else {
                "host"
            },
            label,
            current: progress_span.completed_before + artifact.size,
            total: progress_span.total,
            bytes: true,
        });
        return Ok(destination.to_owned());
    }
    let partial = destination.with_extension("zip.part");
    let _ = fs::remove_file(&partial);
    let mut input = File::open(source)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    let mut output = options.open(&partial)?;
    let mut digest = Sha256::new();
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
        digest.update(&buffer[..count]);
        if downloaded == artifact.size
            || downloaded.saturating_sub(last_reported) >= 4 * 1024 * 1024
        {
            progress(InstallProgress {
                stage: "download",
                component: if label.contains("private") {
                    "guest"
                } else {
                    "host"
                },
                label,
                current: progress_span.completed_before + downloaded,
                total: progress_span.total,
                bytes: true,
            });
            last_reported = downloaded;
        }
    }
    output.sync_all()?;
    if downloaded != artifact.size || format!("{:x}", digest.finalize()) != artifact.sha256 {
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

fn hash_prefix(path: &Path, bytes: u64, digest: &mut Sha256) -> io::Result<()> {
    let mut file = File::open(path)?;
    let mut remaining = bytes;
    let mut buffer = [0_u8; 1024 * 1024];
    while remaining > 0 {
        let wanted = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| invalid("artifact resume length overflow"))?;
        let count = file.read(&mut buffer[..wanted])?;
        if count == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "partial artifact changed while resuming",
            ));
        }
        digest.update(&buffer[..count]);
        remaining -= count as u64;
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn extract_archive(
    path: &Path,
    destination: &Path,
    expected_expanded_size: u64,
    stage: &str,
    component: &str,
    label: &str,
    progress_span: ProgressSpan,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> io::Result<()> {
    fs::create_dir_all(destination)?;
    let mut archive = zip::ZipArchive::new(File::open(path)?)
        .map_err(|error| invalid(format!("invalid ZIP archive: {error}")))?;
    let decompressed_size = archive.decompressed_size().unwrap_or(u128::MAX);
    if archive.len() > MAX_ARCHIVE_ENTRIES
        || decompressed_size > MAX_EXTRACTED_BYTES
        || archive
            .has_overlapping_files()
            .map_err(|error| invalid(format!("invalid ZIP layout: {error}")))?
    {
        return Err(invalid("ZIP archive exceeds safe extraction limits"));
    }
    if decompressed_size != u128::from(expected_expanded_size) {
        return Err(invalid(
            "ZIP expanded size does not match the runtime manifest",
        ));
    }
    let archive_len = archive.len();
    let mut seen = HashSet::new();
    let mut extracted = 0_u128;
    let mut last_reported = 0_u64;
    for index in 0..archive_len {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| invalid(format!("invalid ZIP entry: {error}")))?;
        let relative = entry
            .enclosed_name()
            .ok_or_else(|| invalid("ZIP entry escapes the installation directory"))?;
        // Compared case-insensitively because the filesystems this lands on
        // are. Two entries differing only in case -- LICENSE and license, which
        // npm and Python packages produce routinely -- passed a byte-exact
        // check and then collided at `create_new`, failing the first-run
        // install with a bare "os error 80" that named no file, after which the
        // staging directory was discarded so every retry did the same.
        let key = relative.to_string_lossy().to_lowercase();
        if relative.as_os_str().is_empty() || !seen.insert(key) {
            return Err(invalid(format!(
                "ZIP archive contains an empty or duplicate path: {}",
                relative.display()
            )));
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
        let copied = if output.extension().and_then(|value| value.to_str()) == Some("raw") {
            copy_sparse(&mut entry, &mut output_file)?
        } else {
            io::copy(&mut entry, &mut output_file)?
        };
        if copied != entry.size() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "ZIP entry size changed during extraction",
            ));
        }
        let extracted_u64 =
            u64::try_from(extracted).map_err(|_| invalid("ZIP extracted-size overflow"))?;
        if extracted_u64.saturating_sub(last_reported) >= 16 * 1024 * 1024
            || index + 1 == archive_len
        {
            progress(InstallProgress {
                stage,
                component,
                label,
                current: progress_span.completed_before + extracted_u64,
                total: progress_span.total,
                bytes: true,
            });
            last_reported = extracted_u64;
        }
    }
    sync_directory(destination)?;
    Ok(())
}

fn copy_sparse(input: &mut impl Read, output: &mut File) -> io::Result<u64> {
    let mut copied = 0_u64;
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = input.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        if buffer[..count].iter().all(|byte| *byte == 0) {
            output.seek(SeekFrom::Current(count as i64))?;
        } else {
            output.write_all(&buffer[..count])?;
        }
        copied = copied
            .checked_add(count as u64)
            .ok_or_else(|| invalid("sparse extraction byte count overflow"))?;
    }
    output.set_len(copied)?;
    Ok(copied)
}

fn preflight_free_space(install_root: &Path, expanded_total: u64) -> io::Result<()> {
    fs::create_dir_all(install_root)?;
    let required = expanded_total
        .checked_add(OPERATING_HEADROOM_BYTES)
        .ok_or_else(|| invalid("runtime free-space requirement overflow"))?;
    let available = available_space(install_root)?;
    if available < required {
        return Err(io::Error::other(format!(
            "not enough disk space for Lemma's local runtime: {} GiB required, {} GiB available",
            required.div_ceil(1024 * 1024 * 1024),
            available / (1024 * 1024 * 1024)
        )));
    }
    Ok(())
}

#[cfg(unix)]
fn available_space(path: &Path) -> io::Result<u64> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let path = CString::new(path.as_os_str().as_bytes())
        .map_err(|_| invalid("runtime install path contains a NUL byte"))?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // SAFETY: `path` is NUL-terminated and `stats` points to writable memory.
    let result = unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: statvfs initialized `stats` when it returned success.
    let stats = unsafe { stats.assume_init() };
    Ok((stats.f_bavail as u64).saturating_mul(stats.f_frsize))
}

#[cfg(windows)]
fn available_space(path: &Path) -> io::Result<u64> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;

    let mut path = path.as_os_str().encode_wide().collect::<Vec<_>>();
    path.push(0);
    let mut available = 0_u64;
    // SAFETY: `path` is NUL-terminated and `available` is a valid output pointer.
    let result = unsafe {
        GetDiskFreeSpaceExW(
            path.as_ptr(),
            &mut available,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    if result == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(available)
}

fn sync_directory(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        File::open(path)?.sync_all()
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(())
    }
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
    read_installed_artifacts(root).is_some_and(|actual| actual == *expected)
}

fn read_installed_artifacts(root: &Path) -> Option<InstalledArtifactIdentity> {
    fs::read(root.join(INSTALLED_ARTIFACTS_FILE))
        .ok()
        .and_then(|raw| serde_json::from_slice::<InstalledArtifactIdentity>(&raw).ok())
}

fn valid_recorded_digest(digest: &str) -> bool {
    digest.len() == 64
        && digest.bytes().any(|byte| byte != b'0')
        && digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
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

fn host_platform() -> &'static str {
    #[cfg(target_os = "macos")]
    {
        "macos"
    }
    #[cfg(target_os = "windows")]
    {
        "windows"
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        "unsupported"
    }
}

fn host_architecture() -> &'static str {
    #[cfg(target_arch = "aarch64")]
    {
        "aarch64"
    }
    #[cfg(target_arch = "x86_64")]
    {
        "x86_64"
    }
    #[cfg(not(any(target_arch = "aarch64", target_arch = "x86_64")))]
    {
        "unsupported"
    }
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

    fn extract_for_test(path: &Path, destination: &Path) -> io::Result<()> {
        let expanded_size = zip::ZipArchive::new(File::open(path).unwrap())
            .unwrap()
            .decompressed_size()
            .unwrap() as u64;
        extract_archive(
            path,
            destination,
            expanded_size,
            "extract",
            "test",
            "Extracting test runtime",
            ProgressSpan {
                completed_before: 0,
                total: MAX_EXTRACTED_BYTES as u64,
            },
            &mut |_| {},
        )
    }

    #[test]
    fn extraction_rejects_an_incorrect_signed_expanded_size() {
        let root = tempfile::tempdir().unwrap();
        let archive = root.path().join("runtime.zip");
        write_zip(&archive, &[("runtime/file", b"payload", 0o644)]);

        let error = extract_archive(
            &archive,
            &root.path().join("expanded"),
            1,
            "extract",
            "test",
            "Extracting test runtime",
            ProgressSpan {
                completed_before: 0,
                total: MAX_EXTRACTED_BYTES as u64,
            },
            &mut |_| {},
        )
        .unwrap_err();

        assert!(error.to_string().contains("expanded size"));
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

        extract_for_test(&archive, &root.path().join("output")).unwrap();

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
        assert!(extract_for_test(&traversal, &root.path().join("traversal-out")).is_err());
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
        assert!(extract_for_test(&symlink, &root.path().join("symlink-out")).is_err());
    }

    #[test]
    fn manifest_requires_https_digest_size_format_and_safe_release_name() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("lemma-local.json");
        let artifact = serde_json::json!({
            "url": "https://downloads.example.test/runtime.zip",
            "sha256": "a".repeat(64),
            "size": 42,
            "expanded_size": 84,
            "format": "zip",
            "platform": "macos",
            "architecture": "aarch64",
            "runtime_version": "1.2.3",
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
    fn a_ci_build_check_manifest_is_refused_by_name_not_by_connection_error() {
        // The CI desktop job builds a real, signed, launchable DMG whose
        // manifest points nowhere. Without the marker check the first launch
        // reports "could not connect to the artifact host", which reads as a
        // network fault and costs whoever hit it an hour.
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("lemma-local.json");
        let artifact = serde_json::json!({
            "url": "https://downloads.example.invalid/lemma-host-pack.zip",
            "sha256": "0".repeat(64),
            "size": 1,
            "expanded_size": 1,
            "format": "zip",
            "platform": "macos",
            "architecture": "aarch64",
            "runtime_version": "1.2.3",
        });
        fs::write(
            &path,
            serde_json::to_vec(&serde_json::json!({
                "schema_version": 1,
                "version": "1.2.3",
                "artifact_source": "ci-build-check",
                "host_packs": {host_target(): artifact.clone()},
                "guest_runtimes": {guest_target(): artifact},
            }))
            .unwrap(),
        )
        .unwrap();

        let error = load_manifest(&path).expect_err("a build-check manifest must not install");
        let message = error.to_string();
        assert!(
            message.contains("build-check") && message.contains("Release Local Images"),
            "the refusal must name what this is and how to get a real one: {message}"
        );
    }

    #[test]
    fn a_manifest_without_the_marker_is_unaffected() {
        // `artifact_source` is optional and every real manifest omits it today.
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("lemma-local.json");
        let artifact = serde_json::json!({
            "url": "https://downloads.example.test/runtime.zip",
            "sha256": "a".repeat(64),
            "size": 42,
            "expanded_size": 84,
            "format": "zip",
            "platform": "macos",
            "architecture": "aarch64",
            "runtime_version": "1.2.3",
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
    }

    #[test]
    fn local_file_artifacts_are_explicitly_gated_and_still_digest_verified() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("runtime.zip");
        fs::write(&source, b"locally-built-runtime").unwrap();
        let artifact = ArtifactRef {
            url: Some(reqwest::Url::from_file_path(&source).unwrap().to_string()),
            resource: None,
            sha256: file_sha256(&source).unwrap(),
            size: source.metadata().unwrap().len(),
            expanded_size: source.metadata().unwrap().len(),
            format: "zip".into(),
            platform: "macos".into(),
            architecture: "aarch64".into(),
            runtime_version: "1.2.3".into(),
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
            root.path(),
            true,
            &mut |progress| reports.push((progress.current, progress.total)),
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
            root.path(),
            true,
            &mut |_| {},
        )
        .is_err());
    }

    /// Serialises the tests that set process-global environment variables.
    ///
    /// The local-artifact gate reads `LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS` and
    /// `LEMMA_DESKTOP_RELEASE_MANIFEST` from the process, and cargo runs tests
    /// in threads -- so two of these racing would have one clear the other's
    /// variables mid-install and fail for a reason that is not the code.
    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Build a zip in memory from `(path, contents)` pairs.
    fn zip_of(entries: &[(&str, &[u8])]) -> Vec<u8> {
        use std::io::Write as _;
        let mut buffer = std::io::Cursor::new(Vec::new());
        {
            let mut writer = zip::ZipWriter::new(&mut buffer);
            let options: zip::write::FileOptions<'_, ()> = zip::write::FileOptions::default()
                .compression_method(zip::CompressionMethod::Stored);
            for (name, contents) in entries {
                writer.start_file(*name, options).unwrap();
                writer.write_all(contents).unwrap();
            }
            writer.finish().unwrap();
        }
        buffer.into_inner()
    }

    /// The smallest tree `validate_installed` will accept as a host pack.
    ///
    /// Everything sits under `local-runtime/`, because the archive is expanded
    /// into the release root and `installed_runtime` looks for the pack at
    /// `<release>/local-runtime`. Getting that wrong is exactly the mistake
    /// this test exists to catch, and it caught it on the first run.
    fn host_pack_entries(release: &str) -> Vec<(String, Vec<u8>)> {
        let marker = format!("{{\"version\":\"{release}\"}}");
        let pack = format!("{{\"release\":\"{release}\"}}");
        let (python, node) = if cfg!(windows) {
            ("backend/python/python.exe", "frontend/node/node.exe")
        } else {
            ("backend/python/bin/python3", "frontend/node/bin/node")
        };
        vec![
            ("local-runtime/release.json".to_owned(), marker.into_bytes()),
            ("local-runtime/pack.json".to_owned(), pack.into_bytes()),
            (format!("local-runtime/{python}"), b"#!/bin/sh\n".to_vec()),
            (format!("local-runtime/{node}"), b"#!/bin/sh\n".to_vec()),
        ]
    }

    fn guest_runtime_entries() -> Vec<(String, Vec<u8>)> {
        let target = guest_target();
        let marker = format!("{{\"target\":\"{target}\"}}");
        let mut entries = vec![(format!("{target}/runtime.json"), marker.into_bytes())];
        let files: &[&str] = if cfg!(target_os = "macos") {
            &["vmlinuz", "initrd", "disk.raw"]
        } else {
            &["rootfs.tar"]
        };
        for name in files {
            entries.push((format!("{target}/{name}"), b"guest-bytes".to_vec()));
        }
        entries
    }

    /// A manifest entry, as JSON. `ArtifactRef` is deserialize-only, which is
    /// the right shape for a type that only ever reads a signed document.
    fn artifact_for_bytes(
        path: &Path,
        bytes: &[u8],
        expanded: u64,
        platform: &str,
    ) -> serde_json::Value {
        let mut hasher = Sha256::new();
        hasher.update(bytes);
        serde_json::json!({
            "url": reqwest::Url::from_file_path(path).unwrap().to_string(),
            "sha256": format!("{:x}", hasher.finalize()),
            "size": bytes.len() as u64,
            "expanded_size": expanded,
            "format": "zip",
            "platform": platform,
            "architecture": host_architecture(),
            "runtime_version": "1.2.3",
        })
    }

    /// The whole install, end to end, with no network and no VM.
    ///
    /// `install_from_manifest` is the single most consequential function in the
    /// app -- it is what turns a 23 MB download into a working installation --
    /// and nothing exercised it as a unit. Its parts were tested individually
    /// (digest checks, zip safety, the local-artifact gate) while the sequence
    /// they form was proven only by somebody installing the app by hand.
    ///
    /// This runs the real thing over fabricated archives: download, verify,
    /// extract, validate, record identity, activate. Then it runs it again to
    /// prove the second call is free, which is the promise a warm launch
    /// depends on.
    ///
    /// Serialised with the other env-var test in this module: the local
    /// artifact gate reads process-global state, and cargo runs tests in
    /// threads.
    #[test]
    fn a_manifest_installs_end_to_end_and_a_second_install_is_free() {
        let _guard = env_lock();
        let root = tempfile::tempdir().unwrap();
        let release = "1.2.3";

        let host_entries = host_pack_entries(release);
        let host_zip = zip_of(
            &host_entries
                .iter()
                .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
                .collect::<Vec<_>>(),
        );
        let host_expanded: u64 = host_entries.iter().map(|(_, b)| b.len() as u64).sum();
        let host_path = root.path().join("host.zip");
        fs::write(&host_path, &host_zip).unwrap();

        let guest_entries = guest_runtime_entries();
        let guest_zip = zip_of(
            &guest_entries
                .iter()
                .map(|(name, bytes)| (name.as_str(), bytes.as_slice()))
                .collect::<Vec<_>>(),
        );
        let guest_expanded: u64 = guest_entries.iter().map(|(_, b)| b.len() as u64).sum();
        let guest_path = root.path().join("guest.zip");
        fs::write(&guest_path, &guest_zip).unwrap();

        let manifest = serde_json::json!({
            "schema_version": 1,
            "version": release,
            "host_packs": {
                host_target(): artifact_for_bytes(
                    &host_path, &host_zip, host_expanded, host_platform()),
            },
            "guest_runtimes": {
                guest_target(): artifact_for_bytes(
                    &guest_path, &guest_zip, guest_expanded, "linux"),
            },
        });
        let manifest_path = root.path().join("lemma-local.json");
        fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();

        // The only way a `file://` artifact is honoured, and only for this
        // exact manifest.
        std::env::set_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS", "1");
        std::env::set_var("LEMMA_DESKTOP_RELEASE_MANIFEST", &manifest_path);

        let install_root = root.path().join("runtime");
        let mut stages = Vec::new();
        let installed = install_from_manifest(&manifest_path, &install_root, release, &mut |p| {
            stages.push(p.stage.to_owned());
        })
        .expect("a well-formed manifest installs");

        assert!(installed.is_complete(), "the installed tree validates");
        assert!(installed.has_recorded_artifact_identity());
        assert_eq!(installed.release, release);
        assert!(
            installed.host_pack_root.join("release.json").is_file(),
            "the host pack is extracted where locald looks for it",
        );
        assert!(installed
            .managed_runtime_root
            .join(guest_target())
            .join("runtime.json")
            .is_file());
        // Real progress, in order, not a bar that jumps to done.
        for stage in ["download", "verify", "host-extract", "guest-extract", "validate"] {
            assert!(stages.iter().any(|seen| seen == stage), "missing {stage}: {stages:?}");
        }
        // The archives are cleaned up once they are no longer needed.
        assert!(
            !install_root.join("downloads").join(release).join("host-pack.zip").exists(),
            "a successful install does not leave its downloads behind",
        );

        // A second call must be a no-op. This is what a warm launch depends on:
        // recognising an already-installed runtime by recorded identity rather
        // than re-downloading half a gigabyte.
        let mut second_stages = Vec::new();
        let again = install_from_manifest(&manifest_path, &install_root, release, &mut |p| {
            second_stages.push(p.stage.to_owned());
        })
        .expect("an already-installed runtime is reused");
        assert_eq!(again.host_pack_root, installed.host_pack_root);
        assert!(
            second_stages.is_empty(),
            "reinstalling did work it did not need to: {second_stages:?}",
        );

        std::env::remove_var("LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS");
        std::env::remove_var("LEMMA_DESKTOP_RELEASE_MANIFEST");
    }

    /// A manifest whose version disagrees with the app installs nothing.
    #[test]
    fn a_manifest_for_another_release_is_refused_before_anything_is_downloaded() {
        let _guard = env_lock();
        let root = tempfile::tempdir().unwrap();
        let manifest_path = root.path().join("lemma-local.json");
        fs::write(
            &manifest_path,
            serde_json::to_vec(&serde_json::json!({
                "schema_version": 1,
                "version": "9.9.9",
                "host_packs": {},
                "guest_runtimes": {},
            }))
            .unwrap(),
        )
        .unwrap();

        let error = install_from_manifest(
            &manifest_path,
            &root.path().join("runtime"),
            "1.2.3",
            &mut |_| {},
        )
        .unwrap_err();
        assert!(error.to_string().contains("does not match"), "{error}");
        assert!(
            !root.path().join("runtime/downloads").exists(),
            "nothing is fetched for a runtime this app cannot use",
        );
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
            url: Some("https://downloads.example.test/host.zip".into()),
            resource: None,
            sha256: "a".repeat(64),
            size: 42,
            expanded_size: 84,
            format: "zip".into(),
            platform: "macos".into(),
            architecture: "aarch64".into(),
            runtime_version: "1.2.3".into(),
        };
        let guest = ArtifactRef {
            url: Some("https://downloads.example.test/guest.zip".into()),
            resource: None,
            sha256: "b".repeat(64),
            size: 84,
            expanded_size: 168,
            format: "zip".into(),
            platform: "linux".into(),
            architecture: "aarch64".into(),
            runtime_version: "1.2.3".into(),
        };
        let expected = artifact_identity("1.2.3", &host, &guest);

        assert!(runtime.is_complete());
        assert!(!runtime.has_recorded_artifact_identity());
        assert!(!installed_artifacts_match(&release, &expected));
        write_installed_artifacts(&release, &expected).unwrap();
        assert!(runtime.has_recorded_artifact_identity());
        assert!(installed_artifacts_match(&release, &expected));

        let changed = InstalledArtifactIdentity {
            guest_sha256: "c".repeat(64),
            ..expected
        };
        assert!(!installed_artifacts_match(&release, &changed));
    }

    #[test]
    fn recorded_runtime_identity_rejects_placeholders_and_wrong_targets() {
        let root = tempfile::tempdir().unwrap();
        let release = root.path().join("1.2.3");
        let runtime = complete_runtime(&release, "1.2.3");
        let placeholder = InstalledArtifactIdentity {
            schema_version: MANIFEST_SCHEMA_VERSION,
            release: "1.2.3".into(),
            host_target: host_target().into(),
            host_sha256: "0".repeat(64),
            host_size: 1,
            guest_target: guest_target().into(),
            guest_sha256: "0".repeat(64),
            guest_size: 1,
        };
        write_installed_artifacts(&release, &placeholder).unwrap();
        assert!(!runtime.has_recorded_artifact_identity());

        fs::remove_file(release.join(INSTALLED_ARTIFACTS_FILE)).unwrap();
        let wrong_target = InstalledArtifactIdentity {
            host_sha256: "a".repeat(64),
            guest_sha256: "b".repeat(64),
            host_target: "another-host".into(),
            ..placeholder
        };
        write_installed_artifacts(&release, &wrong_target).unwrap();
        assert!(!runtime.has_recorded_artifact_identity());
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
