//! File ops: stat, list, mkdir, ranged read, chunked write, move, delete, and
//! delivering a secret.
//!
//! Each runs on the blocking pool; a 1 MiB read or a digest over a large file
//! is not something to do on a runtime worker.

use std::collections::HashMap;
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::paths::{Leaf, PathPolicy};
use super::process::decode;
use super::wire::{OP_MAX_DATA_BYTES, OpFailure, kind};

/// Files larger than this are `stat`ed without a digest: hashing a
/// multi-gigabyte build artefact to answer "does it exist" is not worth it.
pub const STAT_DIGEST_LIMIT: u64 = 32 * 1024 * 1024;
/// An upload not finalized within this long is abandoned and its temporary
/// removed.
pub const UPLOAD_TTL: Duration = Duration::from_secs(5 * 60);

/// Uploads in progress: `upload_id` to its temporary and when it was last
/// written.
#[derive(Clone, Default)]
pub struct Uploads {
    open: Arc<Mutex<HashMap<String, (PathBuf, Instant)>>>,
}

impl Uploads {
    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, (PathBuf, Instant)>> {
        self.open.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Remove the temporaries of uploads nobody finished.
    pub fn sweep(&self, ttl: Duration) {
        let now = Instant::now();
        let stale: Vec<PathBuf> = {
            let mut open = self.lock();
            let stale: Vec<String> = open
                .iter()
                .filter(|(_, (_, touched))| now.duration_since(*touched) >= ttl)
                .map(|(id, _)| id.clone())
                .collect();
            stale
                .into_iter()
                .filter_map(|id| open.remove(&id).map(|(path, _)| path))
                .collect()
        };
        for path in stale {
            let _ = std::fs::remove_file(path);
        }
    }

    /// Remove every temporary, for a workspace closing.
    pub fn abandon_all(&self) {
        self.sweep(Duration::ZERO);
    }
}

fn params<T: serde::de::DeserializeOwned>(value: Value) -> Result<T, OpFailure> {
    serde_json::from_value(value).map_err(|error| OpFailure::invalid(error.to_string()))
}

async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, OpFailure> + Send + 'static,
) -> Result<T, OpFailure> {
    tokio::task::spawn_blocking(work)
        .await
        .map_err(|error| OpFailure::new(kind::IO_ERROR, error.to_string()))?
}

fn digest_of(path: &Path) -> std::io::Result<String> {
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 256 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("sha256:{}", hex::encode(hasher.finalize())))
}

/// `sha256:<hex>` or bare hex, compared case-insensitively.
fn same_digest(expected: &str, actual: &str) -> bool {
    let bare = |digest: &str| {
        digest
            .strip_prefix("sha256:")
            .unwrap_or(digest)
            .to_ascii_lowercase()
    };
    bare(expected) == bare(actual)
}

/// A `FileStat`. `digest` asks for `sha256` on a file within the limit.
fn stat(path: &Path, digest: bool) -> Result<Value, OpFailure> {
    let metadata = std::fs::symlink_metadata(path).map_err(|error| OpFailure::io(&error, path))?;
    let file_type = metadata.file_type();
    let kind = if file_type.is_symlink() {
        "symlink"
    } else if file_type.is_dir() {
        "directory"
    } else {
        "file"
    };
    let sha256 = if digest && file_type.is_file() && metadata.len() <= STAT_DIGEST_LIMIT {
        digest_of(path).ok()
    } else {
        None
    };
    let modified_at = metadata
        .modified()
        .ok()
        .map(|modified| chrono::DateTime::<chrono::Utc>::from(modified).to_rfc3339());
    Ok(json!({
        "path": path,
        "kind": kind,
        "size_bytes": metadata.len(),
        "modified_at": modified_at,
        "mode": metadata.mode() & 0o7777,
        "sha256": sha256,
    }))
}

#[derive(Deserialize)]
struct PathParams {
    path: String,
}

pub async fn file_stat(policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
    let request: PathParams = params(raw)?;
    let path = policy.resolve(&request.path, Leaf::NoFollow)?;
    blocking(move || stat(&path, true)).await
}

pub async fn file_list(policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
    let request: PathParams = params(raw)?;
    let path = policy.resolve(&request.path, Leaf::Follow)?;
    blocking(move || {
        let entries = std::fs::read_dir(&path).map_err(|error| OpFailure::io(&error, &path))?;
        let mut stats = Vec::new();
        for entry in entries {
            let entry = entry.map_err(|error| OpFailure::io(&error, &path))?;
            // An entry that vanished between listing and stat is skipped, as
            // `ls` would.
            if let Ok(stat) = stat(&entry.path(), false) {
                stats.push(stat);
            }
        }
        stats.sort_by(|a, b| a["path"].as_str().cmp(&b["path"].as_str()));
        Ok(json!({ "entries": stats }))
    })
    .await
}

pub async fn file_mkdir(policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
    let request: PathParams = params(raw)?;
    let path = policy.resolve(&request.path, Leaf::Follow)?;
    blocking(move || {
        std::fs::create_dir_all(&path).map_err(|error| OpFailure::io(&error, &path))?;
        Ok(json!({}))
    })
    .await
}

pub async fn file_read(policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
    #[derive(Deserialize)]
    struct ReadParams {
        path: String,
        #[serde(default)]
        offset: u64,
        #[serde(default)]
        length: Option<usize>,
    }
    let request: ReadParams = params(raw)?;
    let length = request.length.unwrap_or(OP_MAX_DATA_BYTES);
    if length > OP_MAX_DATA_BYTES {
        return Err(OpFailure::new(
            kind::TOO_LARGE,
            format!("read at most {OP_MAX_DATA_BYTES} bytes at a time"),
        ));
    }
    let path = policy.resolve(&request.path, Leaf::Follow)?;
    blocking(move || {
        let io = |error: std::io::Error| OpFailure::io(&error, &path);
        if path.is_dir() {
            return Err(OpFailure::new(
                kind::IS_A_DIRECTORY,
                format!("{} is a folder", path.display()),
            ));
        }
        let mut file = std::fs::File::open(&path).map_err(io)?;
        let size = file.metadata().map_err(io)?.len();
        file.seek(SeekFrom::Start(request.offset)).map_err(io)?;
        let mut data = Vec::with_capacity(length.min(64 * 1024));
        Read::by_ref(&mut file)
            .take(length as u64)
            .read_to_end(&mut data)
            .map_err(io)?;
        let eof = request.offset + data.len() as u64 >= size;
        Ok(json!({ "data": STANDARD.encode(&data), "eof": eof }))
    })
    .await
}

fn valid_upload_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "_-".contains(character))
}

/// One chunk of an upload. Chunks land in a temporary sibling; `final`
/// verifies the digest, fsyncs and renames it into place, so nobody reading
/// the path ever sees half a file.
pub async fn file_write(
    policy: &PathPolicy,
    uploads: &Uploads,
    raw: Value,
) -> Result<Value, OpFailure> {
    #[derive(Deserialize)]
    struct WriteParams {
        path: String,
        upload_id: String,
        #[serde(default)]
        offset: u64,
        #[serde(default)]
        data: String,
        #[serde(default, rename = "final")]
        last: bool,
        #[serde(default)]
        expected_sha256: Option<String>,
    }
    let request: WriteParams = params(raw)?;
    if !valid_upload_id(&request.upload_id) {
        return Err(OpFailure::invalid(
            "upload_id is 1-64 letters, digits, '_' or '-'",
        ));
    }
    let data = decode(&request.data)?;
    let path = policy.resolve(&request.path, Leaf::Follow)?;
    let Some(name) = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
    else {
        return Err(OpFailure::invalid("a file path is required"));
    };
    let temporary = path.with_file_name(format!(".{name}.lemma-upload-{}", request.upload_id));
    uploads.lock().insert(
        request.upload_id.clone(),
        (temporary.clone(), Instant::now()),
    );
    let uploads = uploads.clone();
    blocking(move || {
        let io = |error: std::io::Error| OpFailure::io(&error, &path);
        if path.is_dir() {
            return Err(OpFailure::new(
                kind::IS_A_DIRECTORY,
                format!("{} is a folder", path.display()),
            ));
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(io)?;
        }
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(&temporary)
            .map_err(io)?;
        file.seek(SeekFrom::Start(request.offset)).map_err(io)?;
        file.write_all(&data).map_err(io)?;
        if !request.last {
            return Ok(json!({}));
        }
        uploads.lock().remove(&request.upload_id);
        let written = file.metadata().map_err(io)?.len();
        let actual = digest_of(&temporary).map_err(io)?;
        if let Some(expected) = &request.expected_sha256
            && !same_digest(expected, &actual)
        {
            let _ = std::fs::remove_file(&temporary);
            return Err(OpFailure::new(
                kind::DIGEST_MISMATCH,
                format!("{written} bytes arrived with digest {actual}, not {expected}"),
            ));
        }
        // Keep the mode of the file being replaced, as an editor would.
        if let Ok(existing) = std::fs::metadata(&path) {
            let _ = std::fs::set_permissions(&temporary, existing.permissions());
        }
        file.sync_all().map_err(io)?;
        drop(file);
        std::fs::rename(&temporary, &path).map_err(|error| {
            let _ = std::fs::remove_file(&temporary);
            io(error)
        })?;
        stat(&path, true)
    })
    .await
}

pub async fn file_move(policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
    #[derive(Deserialize)]
    struct MoveParams {
        source: String,
        destination: String,
    }
    let request: MoveParams = params(raw)?;
    let source = policy.resolve(&request.source, Leaf::NoFollow)?;
    let destination = policy.resolve(&request.destination, Leaf::NoFollow)?;
    blocking(move || {
        std::fs::symlink_metadata(&source).map_err(|error| OpFailure::io(&error, &source))?;
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent).map_err(|error| OpFailure::io(&error, parent))?;
        }
        std::fs::rename(&source, &destination)
            .map_err(|error| OpFailure::io(&error, &destination))?;
        Ok(json!({}))
    })
    .await
}

pub async fn file_delete(policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
    #[derive(Deserialize)]
    struct DeleteParams {
        path: String,
        #[serde(default)]
        recursive: bool,
    }
    let request: DeleteParams = params(raw)?;
    let path = policy.resolve(&request.path, Leaf::NoFollow)?;
    if path == policy.root() {
        return Err(OpFailure::new(
            kind::PERMISSION_DENIED,
            "the workspace root itself cannot be deleted",
        ));
    }
    blocking(move || {
        let metadata = match std::fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(json!({ "existed": false }));
            }
            Err(error) => return Err(OpFailure::io(&error, &path)),
        };
        let removed = if metadata.is_dir() {
            if request.recursive {
                std::fs::remove_dir_all(&path)
            } else {
                std::fs::remove_dir(&path)
            }
        } else {
            std::fs::remove_file(&path)
        };
        removed.map_err(|error| OpFailure::io(&error, &path))?;
        Ok(json!({ "existed": true }))
    })
    .await
}

/// Write a credential for a command to read: the file 0600, a folder created
/// for it 0700, and never a partial file.
pub async fn secret_deliver(policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
    #[derive(Deserialize)]
    struct SecretParams {
        path: String,
        data: String,
    }
    let request: SecretParams = params(raw)?;
    let data = decode(&request.data)?;
    let path = policy.resolve(&request.path, Leaf::Follow)?;
    blocking(move || {
        let io = |error: std::io::Error| OpFailure::io(&error, &path);
        let Some(parent) = path.parent() else {
            return Err(OpFailure::invalid("a file path is required"));
        };
        if !parent.exists() {
            std::fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(parent)
                .map_err(io)?;
        }
        let temporary = path.with_file_name(format!(
            ".{}.lemma-secret-{}",
            path.file_name().unwrap_or_default().to_string_lossy(),
            uuid::Uuid::new_v4()
        ));
        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(&temporary)
            .map_err(io)?;
        let written = file.write_all(&data).and_then(|()| file.sync_all());
        drop(file);
        let renamed = written.and_then(|()| {
            std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o600))?;
            std::fs::rename(&temporary, &path)
        });
        if let Err(error) = renamed {
            let _ = std::fs::remove_file(&temporary);
            return Err(io(error));
        }
        Ok(json!({}))
    })
    .await
}
