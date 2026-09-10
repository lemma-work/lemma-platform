//! Which artifacts this build is allowed to ask for.

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
pub(crate) fn host_target() -> &'static str {
    "aarch64-apple-darwin"
}

pub(crate) fn host_platform() -> &'static str {
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

pub(crate) fn host_architecture() -> &'static str {
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
pub(crate) fn guest_target() -> &'static str {
    "macos-aarch64"
}

#[cfg(all(windows, target_arch = "x86_64"))]
pub(crate) fn host_target() -> &'static str {
    "x86_64-pc-windows-msvc"
}

#[cfg(all(windows, target_arch = "x86_64"))]
pub(crate) fn guest_target() -> &'static str {
    "windows-x86_64"
}

#[cfg(not(any(
    all(target_os = "macos", target_arch = "aarch64"),
    all(windows, target_arch = "x86_64")
)))]
pub(crate) fn host_target() -> &'static str {
    "unsupported"
}

#[cfg(not(any(
    all(target_os = "macos", target_arch = "aarch64"),
    all(windows, target_arch = "x86_64")
)))]
pub(crate) fn guest_target() -> &'static str {
    "unsupported"
}
