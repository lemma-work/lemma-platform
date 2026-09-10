//! Refusing an installation that would fill the disk, rather than filling
//! it and failing part way through.

use super::*;

pub(crate) fn installation_space_required(
    download_total: u64,
    expanded_total: u64,
) -> io::Result<u64> {
    if download_total > MAX_COMBINED_COMPRESSED_BYTES {
        return Err(invalid(
            "combined runtime archives exceed the 6 GiB supported size",
        ));
    }
    if expanded_total > MAX_COMBINED_EXPANDED_BYTES {
        return Err(invalid(
            "expanded immutable runtime exceeds the 8 GiB supported size",
        ));
    }
    // Downloads coexist with extracted candidate files until installation commits.
    download_total
        .checked_add(expanded_total)
        .and_then(|bytes| bytes.checked_add(OPERATING_HEADROOM_BYTES))
        .ok_or_else(|| invalid("runtime free-space requirement overflow"))
}

pub(crate) fn preflight_free_space(install_root: &Path, required: u64) -> io::Result<()> {
    fs::create_dir_all(install_root)?;
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
pub(crate) fn available_space(path: &Path) -> io::Result<u64> {
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
pub(crate) fn available_space(path: &Path) -> io::Result<u64> {
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

pub(crate) fn sync_directory(path: &Path) -> io::Result<()> {
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
