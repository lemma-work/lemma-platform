//! The install's guards, grouped the way the code they cover is grouped.

//! The install's guards, in the file the module keeps them in.

use super::*;
use zip::write::SimpleFileOptions;

mod download;
mod extract;
mod install;
mod installed;
mod layout;
mod manifest;

pub(super) fn write_zip(path: &Path, entries: &[(&str, &[u8], u32)]) {
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

pub(super) fn extract_for_test(path: &Path, destination: &Path) -> io::Result<()> {
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

pub(super) fn complete_runtime(root: &Path, release: &str) -> InstalledRuntime {
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

/// Serialises the tests that set process-global environment variables.
///
/// The local-artifact gate reads `LEMMA_DESKTOP_ALLOW_LOCAL_ARTIFACTS` and
/// `LEMMA_DESKTOP_RELEASE_MANIFEST` from the process, and cargo runs tests
/// in threads -- so two of these racing would have one clear the other's
/// variables mid-install and fail for a reason that is not the code.
pub(super) fn env_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Build a zip in memory from `(path, contents)` pairs.
pub(super) fn zip_of(entries: &[(&str, &[u8])]) -> Vec<u8> {
    use std::io::Write as _;
    let mut buffer = std::io::Cursor::new(Vec::new());
    {
        let mut writer = zip::ZipWriter::new(&mut buffer);
        let options: zip::write::FileOptions<'_, ()> =
            zip::write::FileOptions::default().compression_method(zip::CompressionMethod::Stored);
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
pub(super) fn host_pack_entries(release: &str) -> Vec<(String, Vec<u8>)> {
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

pub(super) fn guest_runtime_entries() -> Vec<(String, Vec<u8>)> {
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
pub(super) fn artifact_for_bytes(
    path: &Path,
    bytes: &[u8],
    expanded: u64,
    platform: &str,
    release: &str,
) -> serde_json::Value {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    serde_json::json!({
        "url": reqwest::Url::from_file_path(path).unwrap().to_string(),
        "sha256": hex::encode(hasher.finalize()),
        "size": bytes.len() as u64,
        "expanded_size": expanded,
        "format": "zip",
        "platform": platform,
        "architecture": host_architecture(),
        "runtime_version": release,
    })
}
