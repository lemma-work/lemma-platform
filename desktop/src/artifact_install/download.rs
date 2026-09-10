//! Fetching one artifact: resumable ranged GETs, a local file copy for a
//! development manifest, and the digest check both end in.

use super::*;

pub(crate) fn download_client() -> io::Result<Client> {
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
pub(crate) fn download_artifact(
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
    if downloaded != artifact.size || hex::encode(digest.finalize()) != artifact.sha256 {
        let _ = fs::remove_file(&partial);
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "artifact size or SHA-256 did not match the signed manifest",
        ));
    }
    replace_archive(&partial, destination)?;
    Ok(destination.to_owned())
}

pub(crate) fn copy_local_artifact(
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

pub(crate) fn copy_artifact_file(
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
    if downloaded != artifact.size || hex::encode(digest.finalize()) != artifact.sha256 {
        let _ = fs::remove_file(&partial);
        return Err(invalid(
            "local artifact SHA-256 did not match the test manifest",
        ));
    }
    replace_archive(&partial, destination)?;
    Ok(destination.to_owned())
}

pub(crate) fn replace_archive(source: &Path, destination: &Path) -> io::Result<()> {
    if destination.exists() {
        fs::rename(destination, quarantine_path(destination)?)?;
    }
    fs::rename(source, destination)
}

pub(crate) fn quarantine_path(path: &Path) -> io::Result<PathBuf> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| invalid("artifact path has no safe file name"))?;
    Ok(path.with_file_name(format!(".{name}.invalid-{}", unix_millis()?)))
}

pub(crate) fn valid_content_range(value: &str, expected_start: u64, expected_size: u64) -> bool {
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

pub(crate) fn archive_matches(path: &Path, artifact: &ArtifactRef) -> io::Result<bool> {
    let Ok(metadata) = path.metadata() else {
        return Ok(false);
    };
    if !metadata.is_file() || metadata.len() != artifact.size {
        return Ok(false);
    }
    Ok(file_sha256(path)? == artifact.sha256)
}

pub(crate) fn file_sha256(path: &Path) -> io::Result<String> {
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
    Ok(hex::encode(digest.finalize()))
}

pub(crate) fn hash_prefix(path: &Path, bytes: u64, digest: &mut Sha256) -> io::Result<()> {
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

pub(crate) fn download_error(error: &reqwest::Error) -> &'static str {
    if error.is_timeout() {
        "artifact download timed out"
    } else if error.is_connect() {
        "could not connect to the artifact host"
    } else {
        "artifact download failed"
    }
}
