//! Expanding a verified archive, within the bounds a manifest declared.

use super::*;

#[allow(clippy::too_many_arguments)]
pub(crate) fn extract_archive(
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

pub(crate) fn copy_sparse(input: &mut impl Read, output: &mut File) -> io::Result<u64> {
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
