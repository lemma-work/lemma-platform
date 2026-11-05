//! Fetching the `lemma` CLI for an app that does not carry one.
//!
//! The online app is too small to carry the CLI (about 90 MiB of standalone
//! Python), so a hosted workspace's run would get none. Instead the host
//! downloads the CLI of its *own* release the first time one is needed, from a
//! manifest whose address is compiled into this binary:
//!
//! - **Which binary is not the backend's to choose.** The manifest URL is a
//!   constant of the release channel this binary was built for, and nothing a
//!   paired server sends changes it. The archive must sit beside the manifest
//!   (same release), match the manifest's size and SHA-256, and be for this
//!   host's exact version and platform.
//! - **Nothing half-written is ever used.** The manifest and archive land in a
//!   `.partial-*` folder, are checked, unpacked there, and the unpacked tree is
//!   renamed to `cli/<version>` in one step. Only that name is ever resolved.
//! - **One download, however many runs ask.** Every caller goes through one
//!   lock per data directory; the first downloads, the rest find it installed.
//! - **A failure costs a run its CLI, not its start.** A run waits a short
//!   while for a download in flight and then goes on without one; a failed
//!   attempt is retried on a later run, after a backoff that grows.
//!
//! Version skew with the server is accepted: the CLI is the host's release,
//! as the bundled one is, and a hosted server may be newer or older.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex as StdMutex, OnceLock};
use std::time::{Duration, Instant};

use anyhow::{Context as _, bail, ensure};
use serde::Deserialize;
use sha2::{Digest as _, Sha256};

/// Names the manifest URL for a debug build. Release builds ignore it: their
/// manifest is the release channel's, compiled in, and a debug build without
/// it downloads nothing (so tests and `cargo run` never reach the network).
pub const MANIFEST_URL_OVERRIDE: &str = "LEMMA_AGENT_HOST_CLI_MANIFEST_URL";

/// The stable channel's manifest: the GitHub release of this exact version.
/// A build for another channel sets `LEMMA_AGENT_HOST_CLI_MANIFEST_URL` at
/// *compile* time (`option_env!`), which is still fixed per binary.
const RELEASE_MANIFEST_URL: &str = "https://github.com/lemma-work/lemma-platform/releases/download/v{version}/lemma-cli-{platform}.json";

/// Platforms a release publishes a CLI archive for
/// (`release-desktop.yml`). Others never try.
const PUBLISHED_PLATFORMS: &[&str] = &["aarch64-apple-darwin"];

/// Where under the host data directory downloaded CLIs live, one folder per
/// version.
pub const FOLDER: &str = "cli";

/// A manifest is a few hundred bytes; anything much larger is not one.
const MAX_MANIFEST_BYTES: u64 = 64 * 1024;
/// The archive is about 30 MiB compressed; this only bounds a broken one.
const MAX_ARCHIVE_BYTES: u64 = 512 * 1024 * 1024;
const PARTIAL_PREFIX: &str = ".partial-";

/// How long a run start waits for a download in flight before going on
/// without a CLI. The download carries on in the background for later runs.
pub const RUN_START_WAIT: Duration = Duration::from_secs(15);
const FIRST_BACKOFF: Duration = Duration::from_secs(30);
const MAX_BACKOFF: Duration = Duration::from_secs(60 * 60);

/// What a release publishes beside each CLI archive
/// (`scripts/build_desktop_lemma_cli.py --archive`).
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct Manifest {
    pub version: String,
    pub platform: String,
    pub url: String,
    pub sha256: String,
    pub size: u64,
}

impl Manifest {
    /// Whether this manifest describes the CLI this host may install: its own
    /// version and platform, an archive in the same release as the manifest,
    /// and a digest and size that can be checked.
    pub fn check(&self, version: &str, platform: &str, manifest_url: &str) -> anyhow::Result<()> {
        ensure!(
            self.version == version,
            "the manifest is for version {}, not this host's {version}",
            self.version
        );
        ensure!(
            self.platform == platform,
            "the manifest is for {}, not {platform}",
            self.platform
        );
        ensure!(
            self.sha256.len() == 64 && self.sha256.bytes().all(|b| b.is_ascii_hexdigit()),
            "the manifest's sha256 is not a SHA-256 digest"
        );
        ensure!(
            self.size > 0 && self.size <= MAX_ARCHIVE_BYTES,
            "the manifest's size {} is out of range",
            self.size
        );
        let release = release_folder(manifest_url)?;
        let archive = self.url.strip_prefix(release).unwrap_or_default();
        ensure!(
            !archive.is_empty() && !archive.contains(['/', '?', '#', '\\']) && archive != "..",
            "the archive {} is not in the same release as its manifest",
            self.url
        );
        Ok(())
    }
}

/// The URL of the folder a manifest is in, with its trailing slash.
///
/// A release build takes only `https`: the manifest carries the archive's
/// digest, so a manifest fetched in the clear protects nothing. A debug
/// build also takes `http`, for a local stand-in release.
fn release_folder(manifest_url: &str) -> anyhow::Result<&str> {
    release_folder_allowing(manifest_url, cfg!(debug_assertions))
}

fn release_folder_allowing(manifest_url: &str, allow_http: bool) -> anyhow::Result<&str> {
    let parsed = url::Url::parse(manifest_url).context("the manifest URL is not a URL")?;
    let scheme_ok = parsed.scheme() == "https" || (allow_http && parsed.scheme() == "http");
    ensure!(
        scheme_ok && parsed.query().is_none(),
        "the manifest URL must be a plain https URL"
    );
    let end = manifest_url
        .rfind('/')
        .context("the manifest URL has no path")?;
    Ok(&manifest_url[..=end])
}

/// This build's target, as the release names its archives.
#[must_use]
pub fn platform() -> Option<&'static str> {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        Some("aarch64-apple-darwin")
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        Some("x86_64-apple-darwin")
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        Some("x86_64-unknown-linux-gnu")
    } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
        Some("aarch64-unknown-linux-gnu")
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        Some("x86_64-pc-windows-msvc")
    } else {
        None
    }
}

/// The manifest this binary downloads from, if it downloads at all.
#[must_use]
pub fn manifest_url() -> Option<String> {
    let platform = platform()?;
    if cfg!(debug_assertions) {
        // Development: only an explicit override, read at run time.
        return std::env::var(MANIFEST_URL_OVERRIDE)
            .ok()
            .filter(|value| !value.is_empty());
    }
    if !PUBLISHED_PLATFORMS.contains(&platform) {
        return None;
    }
    let template = option_env!("LEMMA_AGENT_HOST_CLI_MANIFEST_URL").unwrap_or(RELEASE_MANIFEST_URL);
    Some(
        template
            .replace("{version}", crate::HOST_RELEASE)
            .replace("{platform}", platform),
    )
}

#[derive(Debug, Default)]
struct Attempts {
    failures: u32,
    next_try: Option<Instant>,
}

/// Downloads and keeps the CLI for one host data directory.
#[derive(Debug)]
pub struct Downloader {
    folder: PathBuf,
    version: String,
    platform: String,
    manifest_url: Option<String>,
    first_backoff: Duration,
    attempts: tokio::sync::Mutex<Attempts>,
}

impl Downloader {
    /// For `data_dir`, downloading this host's release from `manifest_url`
    /// (`None`: never download).
    #[must_use]
    pub fn new(data_dir: &Path, manifest_url: Option<String>) -> Self {
        Self::for_release(
            data_dir,
            manifest_url,
            crate::HOST_RELEASE,
            platform().unwrap_or("unknown"),
        )
    }

    /// As `new`, for a named version and platform. Tests use this.
    #[must_use]
    pub fn for_release(
        data_dir: &Path,
        manifest_url: Option<String>,
        version: &str,
        platform: &str,
    ) -> Self {
        Self {
            folder: data_dir.join(FOLDER),
            version: version.to_owned(),
            platform: platform.to_owned(),
            manifest_url,
            first_backoff: FIRST_BACKOFF,
            attempts: tokio::sync::Mutex::default(),
        }
    }

    /// With a different first retry delay. Tests use this.
    #[must_use]
    pub fn with_backoff(mut self, first: Duration) -> Self {
        self.first_backoff = first;
        self
    }

    /// The one downloader for this process's data directory, so every run
    /// and every pairing shares its lock and its backoff.
    #[must_use]
    pub fn shared(data_dir: &Path) -> Arc<Self> {
        static SHARED: OnceLock<StdMutex<HashMap<PathBuf, Arc<Downloader>>>> = OnceLock::new();
        let mut shared = SHARED
            .get_or_init(StdMutex::default)
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        Arc::clone(
            shared
                .entry(data_dir.to_path_buf())
                .or_insert_with(|| Arc::new(Self::new(data_dir, manifest_url()))),
        )
    }

    /// Where this version is once installed. Only ever made by a rename, so
    /// if it exists it is whole.
    #[must_use]
    pub fn installed_dir(&self) -> PathBuf {
        self.folder.join(&self.version)
    }

    fn is_installed(&self) -> bool {
        self.installed_dir().join("bin/lemma").is_file()
    }

    /// Whether this downloader would ever fetch anything.
    #[must_use]
    pub fn enabled(&self) -> bool {
        self.manifest_url.is_some()
    }

    /// The installed CLI, downloading it first if it is missing and no
    /// recent failure says to wait. Concurrent callers share one download.
    pub async fn ensure(&self) -> Option<PathBuf> {
        if self.is_installed() {
            return Some(self.installed_dir());
        }
        let manifest_url = self.manifest_url.as_deref()?;
        let mut attempts = self.attempts.lock().await;
        if self.is_installed() {
            return Some(self.installed_dir());
        }
        if attempts.next_try.is_some_and(|next| Instant::now() < next) {
            return None;
        }
        match self.download(manifest_url).await {
            Ok(installed) => {
                *attempts = Attempts::default();
                tracing::info!(
                    version = %self.version,
                    path = %installed.display(),
                    "downloaded the lemma CLI for hosted runs"
                );
                self.remove_stale();
                Some(installed)
            }
            Err(error) => {
                attempts.failures = attempts.failures.saturating_add(1);
                let delay = backoff(self.first_backoff, attempts.failures);
                attempts.next_try = Some(Instant::now() + delay);
                tracing::warn!(
                    error = format!("{error:#}"),
                    manifest = manifest_url,
                    failures = attempts.failures,
                    retry_after_seconds = delay.as_secs(),
                    "could not download the lemma CLI; hosted runs go without one until a later run retries"
                );
                None
            }
        }
    }

    /// The installed CLI, waiting at most `wait` for a download to finish.
    /// A download still going when the wait ends carries on for later runs.
    pub async fn ready_within(self: &Arc<Self>, wait: Duration) -> Option<PathBuf> {
        if self.is_installed() {
            return Some(self.installed_dir());
        }
        if !self.enabled() {
            return None;
        }
        let downloader = Arc::clone(self);
        let attempt = tokio::spawn(async move { downloader.ensure().await });
        tokio::time::timeout(wait, attempt)
            .await
            .ok()
            .and_then(Result::ok)
            .flatten()
    }

    /// Starts a download in the background if one is needed, so the CLI is
    /// usually there before the first run asks.
    pub fn prefetch(self: &Arc<Self>) {
        if self.is_installed() || !self.enabled() {
            return;
        }
        let downloader = Arc::clone(self);
        tokio::spawn(async move { downloader.ensure().await });
    }

    /// Removes every other version, and anything a crashed download left.
    /// Called with the lock held, or before any download can start.
    pub fn remove_stale(&self) {
        let Ok(entries) = std::fs::read_dir(&self.folder) else {
            return;
        };
        for entry in entries.flatten() {
            if entry.file_name() == self.version.as_str() {
                continue;
            }
            let path = entry.path();
            let removed = if path.is_dir() && !path.is_symlink() {
                std::fs::remove_dir_all(&path)
            } else {
                std::fs::remove_file(&path)
            };
            if let Err(error) = removed {
                tracing::warn!(%error, path = %path.display(), "could not remove an old lemma CLI");
            }
        }
    }

    async fn download(&self, manifest_url: &str) -> anyhow::Result<PathBuf> {
        std::fs::create_dir_all(&self.folder)
            .with_context(|| format!("could not create {}", self.folder.display()))?;
        let staging = Staging::new(
            self.folder
                .join(format!("{PARTIAL_PREFIX}{}", uuid::Uuid::new_v4())),
        )?;
        let manifest_path = staging.path.join("manifest.json");
        fetch(
            manifest_url,
            &manifest_path,
            MAX_MANIFEST_BYTES,
            manifest_url,
        )
        .await?;
        let manifest: Manifest = serde_json::from_slice(
            &std::fs::read(&manifest_path).context("could not read the downloaded manifest")?,
        )
        .context("the manifest is not a CLI manifest")?;
        manifest.check(&self.version, &self.platform, manifest_url)?;

        let archive = staging.path.join("lemma-cli.tar.gz");
        fetch(&manifest.url, &archive, manifest.size, manifest_url).await?;
        let (size, digest) = {
            let archive = archive.clone();
            tokio::task::spawn_blocking(move || sha256_file(&archive)).await??
        };
        ensure!(
            size == manifest.size,
            "the archive is {size} bytes, not the manifest's {}",
            manifest.size
        );
        ensure!(
            digest.eq_ignore_ascii_case(&manifest.sha256),
            "the archive's SHA-256 does not match the manifest's; refusing it"
        );

        let tree = staging.path.join("tree");
        std::fs::create_dir(&tree)?;
        extract(&archive, &tree).await?;
        std::fs::remove_file(&archive)?;
        ensure!(
            tree.join("bin/lemma").is_file(),
            "the archive has no bin/lemma"
        );
        let installed = self.installed_dir();
        // Only reachable when the folder is not a whole install (`ensure`
        // checked under the lock), so nothing usable is thrown away.
        if std::fs::symlink_metadata(&installed).is_ok() {
            let _ = std::fs::remove_dir_all(&installed);
        }
        std::fs::rename(&tree, &installed)
            .with_context(|| format!("could not move the CLI into {}", installed.display()))?;
        Ok(installed)
    }
}

fn backoff(first: Duration, failures: u32) -> Duration {
    let doublings = failures.saturating_sub(1).min(16);
    first.saturating_mul(1 << doublings).min(MAX_BACKOFF)
}

/// A `.partial-*` folder, removed however the download ends.
struct Staging {
    path: PathBuf,
}

impl Staging {
    fn new(path: PathBuf) -> anyhow::Result<Self> {
        std::fs::create_dir(&path)
            .with_context(|| format!("could not create {}", path.display()))?;
        Ok(Self { path })
    }
}

impl Drop for Staging {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

fn sha256_file(path: &Path) -> anyhow::Result<(u64, String)> {
    use std::io::Read as _;
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1 << 16];
    let mut size = 0_u64;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        size += read as u64;
        hasher.update(&buffer[..read]);
    }
    Ok((size, hex::encode(hasher.finalize())))
}

/// The system's own tool, by absolute path where the OS guarantees one.
fn system_tool(name: &str) -> PathBuf {
    let absolute = Path::new("/usr/bin").join(name);
    if cfg!(target_os = "macos") || absolute.is_file() {
        absolute
    } else {
        PathBuf::from(name)
    }
}

/// Downloads `url` to `output` with the system `curl`: it honours the
/// machine's proxy settings, which a desktop behind a corporate proxy needs.
/// `-q` first, so no `~/.curlrc` can turn verification off; HTTPS only, through
/// every redirect, whenever the manifest itself is HTTPS.
async fn fetch(url: &str, output: &Path, max_bytes: u64, manifest_url: &str) -> anyhow::Result<()> {
    let https = manifest_url.starts_with("https://");
    ensure!(
        !https || url.starts_with("https://"),
        "refusing to download over plain HTTP"
    );
    let protocols = if https { "=https" } else { "=http,https" };
    let result = tokio::process::Command::new(system_tool("curl"))
        .arg("-q")
        .args(["--fail", "--location", "--silent", "--show-error"])
        .args(["--proto", protocols, "--proto-redir", protocols])
        .args(["--connect-timeout", "20", "--max-time", "900"])
        .args(["--retry", "2"])
        .arg("--max-filesize")
        .arg(max_bytes.to_string())
        .arg("--output")
        .arg(output)
        .arg(url)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true)
        .output()
        .await
        .context("could not run curl")?;
    if !result.status.success() {
        let stderr = String::from_utf8_lossy(&result.stderr);
        bail!(
            "downloading {url} failed ({}): {}",
            result.status,
            stderr.trim().chars().take(300).collect::<String>()
        );
    }
    let size = std::fs::metadata(output)
        .with_context(|| format!("{url} downloaded nothing"))?
        .len();
    ensure!(size <= max_bytes, "{url} is larger than {max_bytes} bytes");
    Ok(())
}

/// Unpacks a verified archive. Its digest is checked first, so this is the
/// release's own tree; `tar` still refuses absolute and `..` paths.
async fn extract(archive: &Path, into: &Path) -> anyhow::Result<()> {
    let result = tokio::process::Command::new(system_tool("tar"))
        .arg("-xzf")
        .arg(archive)
        .arg("-C")
        .arg(into)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true)
        .output()
        .await
        .context("could not run tar")?;
    if !result.status.success() {
        bail!(
            "unpacking the CLI failed ({}): {}",
            result.status,
            String::from_utf8_lossy(&result.stderr)
                .trim()
                .chars()
                .take(300)
                .collect::<String>()
        );
    }
    Ok(())
}

#[cfg(all(test, unix))]
#[path = "download_tests.rs"]
mod tests;
