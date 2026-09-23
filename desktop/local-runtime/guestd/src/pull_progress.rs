//! How far an image download has got, in bytes.
//!
//! `nerdctl pull --quiet` says nothing until it finishes, and a first start
//! after an update waits minutes on a workspace image with only "still
//! downloading" to show for it. containerd knows better: the image's manifest is
//! fetched first and names every layer's size, finished layers are in the
//! content store, and a layer being written is an "active" ingest with its
//! offset. Reading those three gives a real `done of total`.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Seek, SeekFrom};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

/// Bytes fetched and bytes in total, for an image being pulled.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct PullProgress {
    pub(crate) done: u64,
    pub(crate) total: u64,
}

impl PullProgress {
    /// "412 MB of 980 MB", as a person reads it and the backend parses it.
    pub(crate) fn sentence(self) -> String {
        format!(
            "{} MB of {} MB",
            mebibytes(self.done),
            mebibytes(self.total)
        )
    }
}

fn mebibytes(bytes: u64) -> u64 {
    bytes.div_ceil(1024 * 1024)
}

pub(crate) fn pull_progress() -> &'static Mutex<HashMap<String, PullProgress>> {
    static PROGRESS: OnceLock<Mutex<HashMap<String, PullProgress>>> = OnceLock::new();
    PROGRESS.get_or_init(|| Mutex::new(HashMap::new()))
}

pub(crate) fn progress_for(image: &str) -> Option<PullProgress> {
    pull_progress()
        .lock()
        .expect("pull progress poisoned")
        .get(image)
        .copied()
}

/// Sample `image`'s download about once a second until the guard is dropped.
pub(crate) struct Sampler {
    stop: Arc<AtomicBool>,
    image: String,
    handle: Option<thread::JoinHandle<()>>,
}

impl Sampler {
    pub(crate) fn start(image: &str) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let owned = image.to_owned();
        let flag = Arc::clone(&stop);
        let handle = thread::Builder::new()
            .name("lemma-guest-pull-progress".into())
            .spawn(move || {
                while !flag.load(Ordering::Acquire) {
                    if let Some(progress) = sample(&owned) {
                        pull_progress()
                            .lock()
                            .expect("pull progress poisoned")
                            .insert(owned.clone(), progress);
                    }
                    thread::sleep(Duration::from_secs(1));
                }
            })
            .ok();
        Self {
            stop,
            image: image.to_owned(),
            handle,
        }
    }
}

impl Drop for Sampler {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
        pull_progress()
            .lock()
            .expect("pull progress poisoned")
            .remove(&self.image);
    }
}

const CTR: &str = "/usr/bin/ctr";
const CONTAINERD_SOCKET: &str = "/run/containerd/containerd.sock";

/// Longest a single `ctr` query may take. The sampler checks for its stop only
/// between queries, so an unbounded one could hold the pull's return behind it.
const CTR_TIMEOUT: Duration = Duration::from_secs(3);

fn ctr(arguments: &[&str]) -> Option<Vec<u8>> {
    // A file, not a pipe: `content ls` can outgrow a pipe's buffer, and a child
    // blocked writing to a pipe nobody reads until it exits never exits.
    let mut stdout = tempfile::tempfile().ok()?;
    let mut child = Command::new(CTR)
        .args(["--address", CONTAINERD_SOCKET, "--namespace", "lemma"])
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout.try_clone().ok()?))
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let deadline = Instant::now() + CTR_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    if !status.success() {
        return None;
    }
    let mut output = Vec::new();
    stdout.seek(SeekFrom::Start(0)).ok()?;
    stdout.read_to_end(&mut output).ok()?;
    Some(output)
}

fn sample(image: &str) -> Option<PullProgress> {
    let digest = image.rsplit_once('@')?.1;
    let top: Value = serde_json::from_slice(&ctr(&["content", "get", digest])?).ok()?;
    let manifest = match platform_manifest(&top, crate::sandbox_run::guest_platform()) {
        Some(child) => serde_json::from_slice(&ctr(&["content", "get", &child])?).ok()?,
        None => top,
    };
    let blobs = manifest_blobs(&manifest)?;
    let stored: HashSet<String> = String::from_utf8_lossy(&ctr(&["content", "ls", "-q"])?)
        .lines()
        .map(|line| line.trim().to_owned())
        .collect();
    let active = parse_active(&String::from_utf8_lossy(
        &ctr(&["content", "active"]).unwrap_or_default(),
    ));
    Some(progress(&blobs, &stored, &active))
}

/// The manifest for `platform` inside an index, or `None` if `index` is itself
/// a manifest.
pub(crate) fn platform_manifest(index: &Value, platform: &str) -> Option<String> {
    let manifests = index.get("manifests")?.as_array()?;
    let (os, architecture) = platform.split_once('/')?;
    manifests
        .iter()
        .find(|entry| {
            let wanted = entry.get("platform");
            wanted.and_then(|p| p.get("os")).and_then(Value::as_str) == Some(os)
                && wanted
                    .and_then(|p| p.get("architecture"))
                    .and_then(Value::as_str)
                    == Some(architecture)
        })
        .and_then(|entry| entry.get("digest")?.as_str().map(str::to_owned))
}

/// Every blob a manifest names, with its size: the config and each layer.
pub(crate) fn manifest_blobs(manifest: &Value) -> Option<Vec<(String, u64)>> {
    let mut blobs = Vec::new();
    let entries = manifest
        .get("config")
        .into_iter()
        .chain(manifest.get("layers")?.as_array()?.iter());
    for entry in entries {
        let digest = entry.get("digest")?.as_str()?.to_owned();
        let size = entry.get("size")?.as_u64()?;
        blobs.push((digest, size));
    }
    Some(blobs)
}

/// `ctr content active`: a header, then `REF SIZE AGE`, the size as
/// `12.3 MiB` or `512B`. The ref names the blob: `layer-sha256:<hex>`.
pub(crate) fn parse_active(text: &str) -> HashMap<String, u64> {
    let mut active = HashMap::new();
    for line in text.lines().skip(1) {
        let fields: Vec<&str> = line.split_whitespace().collect();
        let (Some(reference), Some(first)) = (fields.first(), fields.get(1)) else {
            continue;
        };
        let Some(start) = reference.find("sha256:") else {
            continue;
        };
        // The size is either one field (`512B`, `3.1MiB`) or a number and a
        // unit (`3.1 MiB`), depending on the humanizer's spacing.
        let bytes = match fields.get(2) {
            Some(unit) if unit.chars().all(char::is_alphabetic) => {
                parse_size(&format!("{first}{unit}"))
            }
            _ => parse_size(first),
        };
        if let Some(bytes) = bytes {
            active.insert(reference[start..].to_owned(), bytes);
        }
    }
    active
}

pub(crate) fn parse_size(text: &str) -> Option<u64> {
    let split = text
        .find(|character: char| !(character.is_ascii_digit() || character == '.'))
        .unwrap_or(text.len());
    let (number, unit) = text.split_at(split);
    let value: f64 = number.parse().ok()?;
    let scale: f64 = match unit.trim() {
        "" | "B" => 1.0,
        "kB" | "KB" => 1e3,
        "KiB" => 1024.0,
        "MB" => 1e6,
        "MiB" => 1024.0 * 1024.0,
        "GB" => 1e9,
        "GiB" => 1024.0 * 1024.0 * 1024.0,
        _ => return None,
    };
    // Truncating a byte count estimate is exactly what is wanted.
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    Some((value * scale) as u64)
}

/// Stored blobs count whole, blobs being written count up to their offset,
/// and nothing counts past its own size.
pub(crate) fn progress(
    blobs: &[(String, u64)],
    stored: &HashSet<String>,
    active: &HashMap<String, u64>,
) -> PullProgress {
    let total = blobs.iter().map(|(_, size)| size).sum();
    let done = blobs
        .iter()
        .map(|(digest, size)| {
            if stored.contains(digest) {
                *size
            } else {
                active.get(digest).copied().unwrap_or(0).min(*size)
            }
        })
        .sum();
    PullProgress { done, total }
}
