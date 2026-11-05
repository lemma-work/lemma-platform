//! The downloader against a local HTTP server standing in for a GitHub
//! release: one manifest, one archive, and a count of what was fetched.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use axum::Router;
use axum::extract::State;
use axum::routing::get;
use serde_json::json;

use super::{Downloader, Manifest};

const VERSION: &str = "9.8.7";
const PLATFORM: &str = "aarch64-apple-darwin";

/// A gzipped tar of a CLI tree: `bin/lemma` and a file beside it.
fn archive() -> Vec<u8> {
    let directory = tempfile::tempdir().unwrap();
    let tree = directory.path().join("tree");
    std::fs::create_dir_all(tree.join("bin")).unwrap();
    std::fs::create_dir_all(tree.join("cli")).unwrap();
    std::fs::write(tree.join("bin/lemma"), "#!/bin/sh\necho lemma\n").unwrap();
    std::fs::write(tree.join("cli/app.py"), "print('hi')\n").unwrap();
    let out = directory.path().join("cli.tar.gz");
    let status = std::process::Command::new("tar")
        .arg("-czf")
        .arg(&out)
        .arg("-C")
        .arg(&tree)
        .args(["bin", "cli"])
        .status()
        .unwrap();
    assert!(status.success());
    std::fs::read(out).unwrap()
}

fn sha256(bytes: &[u8]) -> String {
    use sha2::Digest as _;
    hex::encode(sha2::Sha256::digest(bytes))
}

struct Release {
    manifest: serde_json::Value,
    /// What `/release/lemma-cli.tar.gz` serves.
    archive: Vec<u8>,
    archive_requests: AtomicUsize,
    manifest_requests: AtomicUsize,
    /// Slows the archive, so concurrent callers overlap a download in flight.
    delay: Duration,
}

/// A release whose manifest describes its archive honestly, then `edit`ed;
/// returned with the manifest's URL.
async fn release_with(
    edit: impl FnOnce(&mut serde_json::Value, &mut Vec<u8>),
) -> (Arc<Release>, String) {
    async fn manifest(State(release): State<Arc<Release>>) -> String {
        release.manifest_requests.fetch_add(1, Ordering::SeqCst);
        release.manifest.to_string()
    }
    async fn archive_route(State(release): State<Arc<Release>>) -> Vec<u8> {
        release.archive_requests.fetch_add(1, Ordering::SeqCst);
        tokio::time::sleep(release.delay).await;
        release.archive.clone()
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let bytes = archive();
    let mut described = json!({
        "version": VERSION,
        "platform": PLATFORM,
        "url": format!("http://{address}/release/lemma-cli.tar.gz"),
        "sha256": sha256(&bytes),
        "size": bytes.len(),
    });
    let mut served = bytes;
    edit(&mut described, &mut served);
    let release = Arc::new(Release {
        manifest: described,
        archive: served,
        archive_requests: AtomicUsize::new(0),
        manifest_requests: AtomicUsize::new(0),
        delay: Duration::from_millis(200),
    });
    let app = Router::new()
        .route("/release/lemma-cli.json", get(manifest))
        .route("/release/lemma-cli.tar.gz", get(archive_route))
        .with_state(Arc::clone(&release));
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (release, format!("http://{address}/release/lemma-cli.json"))
}

fn downloader(data: &Path, manifest_url: &str) -> Arc<Downloader> {
    Arc::new(
        Downloader::for_release(data, Some(manifest_url.to_owned()), VERSION, PLATFORM)
            .with_backoff(Duration::from_millis(300)),
    )
}

fn entries(folder: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(folder)
        .map(|entries| {
            entries
                .flatten()
                .map(|entry| entry.file_name().to_string_lossy().into_owned())
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    names
}

#[tokio::test(flavor = "multi_thread")]
async fn a_matching_archive_is_installed_under_its_version() {
    let data = tempfile::tempdir().unwrap();
    let (release, url) = release_with(|_, _| {}).await;
    let cli = downloader(data.path(), &url);

    let installed = cli.ensure().await.expect("installed");

    assert_eq!(installed, data.path().join("cli").join(VERSION));
    assert!(installed.join("bin/lemma").is_file());
    assert!(installed.join("cli/app.py").is_file());
    // Nothing of the download is left beside it.
    assert_eq!(entries(&data.path().join("cli")), [VERSION]);
    // Installed is installed: no second fetch.
    assert_eq!(cli.ensure().await, Some(installed));
    assert_eq!(release.archive_requests.load(Ordering::SeqCst), 1);
}

#[tokio::test(flavor = "multi_thread")]
async fn an_archive_whose_digest_does_not_match_is_refused() {
    let data = tempfile::tempdir().unwrap();
    let (_release, url) = release_with(|_, archive| {
        // Same length, different bytes: only the digest can tell.
        let last = archive.len() - 1;
        archive[last] ^= 0xff;
    })
    .await;
    let cli = downloader(data.path(), &url);

    assert_eq!(cli.ensure().await, None);
    assert!(!data.path().join("cli").join(VERSION).exists());
    assert!(entries(&data.path().join("cli")).is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_truncated_download_is_never_used() {
    let data = tempfile::tempdir().unwrap();
    let (_release, url) = release_with(|_, archive| {
        archive.truncate(archive.len() / 2);
    })
    .await;
    let cli = downloader(data.path(), &url);

    assert_eq!(cli.ensure().await, None);
    assert!(entries(&data.path().join("cli")).is_empty());
}

/// A crash mid-download leaves a `.partial-*` folder, which may even hold a
/// `bin/lemma`. It is never resolved, and the next clean-up removes it.
#[tokio::test(flavor = "multi_thread")]
async fn a_partial_folder_is_never_the_installed_cli() {
    let data = tempfile::tempdir().unwrap();
    let partial = data.path().join("cli/.partial-crashed");
    std::fs::create_dir_all(partial.join("bin")).unwrap();
    std::fs::write(partial.join("bin/lemma"), "#!/bin/sh\n").unwrap();
    let cli = Arc::new(Downloader::for_release(
        data.path(),
        None,
        VERSION,
        PLATFORM,
    ));

    assert_eq!(cli.ensure().await, None);
    assert_eq!(cli.ready_within(Duration::from_millis(10)).await, None);
    cli.remove_stale();
    assert!(!partial.exists());
}

#[tokio::test(flavor = "multi_thread")]
async fn twenty_concurrent_runs_download_once() {
    let data = tempfile::tempdir().unwrap();
    let (release, url) = release_with(|_, _| {}).await;
    let cli = downloader(data.path(), &url);

    let waits: Vec<_> = (0..20)
        .map(|_| {
            let cli = Arc::clone(&cli);
            tokio::spawn(async move { cli.ensure().await })
        })
        .collect();
    for wait in waits {
        assert_eq!(
            wait.await.unwrap(),
            Some(data.path().join("cli").join(VERSION))
        );
    }
    assert_eq!(release.manifest_requests.load(Ordering::SeqCst), 1);
    assert_eq!(release.archive_requests.load(Ordering::SeqCst), 1);
}

/// A run waits only so long: a download still in flight carries on, and a
/// later run finds it done.
#[tokio::test(flavor = "multi_thread")]
async fn a_run_does_not_wait_long_for_a_download() {
    let data = tempfile::tempdir().unwrap();
    let (_release, url) = release_with(|_, _| {}).await;
    let cli = downloader(data.path(), &url);

    assert_eq!(cli.ready_within(Duration::from_millis(1)).await, None);
    assert_eq!(
        cli.ready_within(Duration::from_secs(30)).await,
        Some(data.path().join("cli").join(VERSION))
    );
}

/// A failure is retried by a later run, but not by every run straight away.
#[tokio::test(flavor = "multi_thread")]
async fn a_failure_backs_off_before_trying_again() {
    let data = tempfile::tempdir().unwrap();
    let (release, url) = release_with(|_, archive| archive.truncate(10)).await;
    let cli = downloader(data.path(), &url);

    assert_eq!(cli.ensure().await, None);
    assert_eq!(cli.ensure().await, None);
    assert_eq!(release.manifest_requests.load(Ordering::SeqCst), 1);

    tokio::time::sleep(Duration::from_millis(400)).await;
    assert_eq!(cli.ensure().await, None);
    assert_eq!(release.manifest_requests.load(Ordering::SeqCst), 2);
}

#[tokio::test(flavor = "multi_thread")]
async fn an_unreachable_manifest_is_a_run_without_a_cli() {
    let data = tempfile::tempdir().unwrap();
    let cli = downloader(data.path(), "http://127.0.0.1:9/release/lemma-cli.json");
    assert_eq!(cli.ensure().await, None);
}

/// Another release's manifest, or one pointing anywhere but its own
/// release, installs nothing.
#[tokio::test(flavor = "multi_thread")]
async fn a_manifest_for_anything_else_is_refused() {
    let edits: [fn(&mut serde_json::Value); 3] = [
        |manifest| manifest["version"] = json!("1.0.0"),
        |manifest| manifest["platform"] = json!("x86_64-pc-windows-msvc"),
        |manifest| manifest["url"] = json!("http://127.0.0.1:9/elsewhere/lemma-cli.tar.gz"),
    ];
    for edit in edits {
        let data = tempfile::tempdir().unwrap();
        let (release, url) = release_with(|manifest, _| edit(manifest)).await;
        let cli = downloader(data.path(), &url);
        assert_eq!(cli.ensure().await, None);
        assert_eq!(release.archive_requests.load(Ordering::SeqCst), 0);
    }
}

#[test]
fn an_archive_must_sit_beside_its_manifest() {
    let manifest = |url: &str| Manifest {
        version: VERSION.into(),
        platform: PLATFORM.into(),
        url: url.into(),
        sha256: "a".repeat(64),
        size: 10,
    };
    let at = "https://github.com/o/r/releases/download/v9.8.7/lemma-cli-x.json";
    let check = |url: &str| manifest(url).check(VERSION, PLATFORM, at);
    assert!(check("https://github.com/o/r/releases/download/v9.8.7/lemma-cli-x.tar.gz").is_ok());
    for refused in [
        "https://github.com/o/r/releases/download/v9.8.6/lemma-cli-x.tar.gz",
        "https://github.com/o/r/releases/download/v9.8.7/../v1/lemma-cli-x.tar.gz",
        "https://github.com/o/r/releases/download/v9.8.7/",
        "https://github.com/o/r/releases/download/v9.8.7/x.tar.gz?u=https://evil",
        "https://evil.example/o/r/releases/download/v9.8.7/lemma-cli-x.tar.gz",
        "http://github.com/o/r/releases/download/v9.8.7/lemma-cli-x.tar.gz",
    ] {
        assert!(check(refused).is_err(), "{refused}");
    }
    let mut bad_digest = manifest("https://github.com/o/r/releases/download/v9.8.7/a.tar.gz");
    bad_digest.sha256 = "not-hex".into();
    assert!(bad_digest.check(VERSION, PLATFORM, at).is_err());
}

/// A release build fetches its manifest only over `https`, since the digest
/// rides in the manifest; a debug build may use a plain-`http` local release.
#[test]
fn a_release_build_takes_only_an_https_manifest() {
    let plain = "http://example.test/release/lemma-cli.json";
    let secure = "https://example.test/release/lemma-cli.json";

    assert!(super::release_folder_allowing(plain, false).is_err());
    assert_eq!(
        super::release_folder_allowing(secure, false).unwrap(),
        "https://example.test/release/"
    );
    assert_eq!(
        super::release_folder_allowing(plain, true).unwrap(),
        "http://example.test/release/"
    );
    assert!(super::release_folder_allowing("ftp://example.test/r/m.json", true).is_err());
    assert!(super::release_folder_allowing("https://example.test/r/m.json?x=1", false).is_err());
}

/// After an upgrade, the previous version's CLI is removed once this one is
/// in place.
#[tokio::test(flavor = "multi_thread")]
async fn an_upgrade_removes_the_old_version() {
    let data = tempfile::tempdir().unwrap();
    let old: PathBuf = data.path().join("cli/9.8.6");
    std::fs::create_dir_all(old.join("bin")).unwrap();
    std::fs::write(old.join("bin/lemma"), "#!/bin/sh\n").unwrap();
    let (_release, url) = release_with(|_, _| {}).await;
    let cli = downloader(data.path(), &url);

    assert!(cli.ensure().await.is_some());
    assert_eq!(entries(&data.path().join("cli")), [VERSION]);
}

#[test]
fn backoff_doubles_up_to_an_hour() {
    let first = Duration::from_secs(30);
    assert_eq!(super::backoff(first, 1), first);
    assert_eq!(super::backoff(first, 2), first * 2);
    assert_eq!(super::backoff(first, 3), first * 4);
    assert_eq!(super::backoff(first, 40), super::MAX_BACKOFF);
}

/// A debug build never downloads unless told where from: tests and
/// `cargo run` stay off the network.
#[test]
fn a_debug_build_downloads_only_when_told_where_from() {
    if cfg!(debug_assertions) && std::env::var_os(super::MANIFEST_URL_OVERRIDE).is_none() {
        assert_eq!(super::manifest_url(), None);
    }
}
