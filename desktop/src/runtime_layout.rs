use super::*;

/// Where the monorepo checkout lives, used only for development fallbacks.
/// Dev default: this repo. Packaged builds set
/// LEMMA_DESKTOP_RUNTIME_ROOT (or persist runtimeRoot in desktop config).
pub(crate) fn runtime_root() -> PathBuf {
    if let Ok(root) = std::env::var("LEMMA_DESKTOP_RUNTIME_ROOT") {
        return PathBuf::from(root);
    }
    if let Some(root) = read_config()["runtimeRoot"].as_str() {
        return PathBuf::from(root);
    }
    default_runtime_root(
        std::env::current_exe().ok().as_deref(),
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")),
        cfg!(debug_assertions),
    )
}

pub(crate) fn default_runtime_root(
    executable: Option<&std::path::Path>,
    manifest_dir: &std::path::Path,
    development: bool,
) -> PathBuf {
    if development {
        // Debug builds may use the monorepo containing this crate. A release
        // build must never trust its compile-time checkout path: that path can
        // still exist on a developer/test machine after the app is copied to
        // Applications, causing the signed package to skip artifact install.
        return manifest_dir
            .parent()
            .expect("desktop crate has a parent directory")
            .to_path_buf();
    }
    executable
        .and_then(std::path::Path::parent)
        .map(std::path::Path::to_path_buf)
        .unwrap_or_default()
}

/// The durable local daemon shipped next to the app executable.
pub(crate) fn bundled_locald() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let candidate = exe.parent()?.join(if cfg!(windows) {
        "lemma-locald.exe"
    } else {
        "lemma-locald"
    });
    candidate.exists().then_some(candidate)
}

/// A development override, honoured only by a development build.
///
/// These three point the app at a different runtime: a host pack, a managed
/// runtime, or the signed manifest that names both and carries the digests
/// everything else is checked against. Each was read unconditionally and took
/// precedence over the bundled resource, so in a shipped, notarized,
/// hardened-runtime app, anything already running as the user could set one and
/// have Lemma download and execute a runtime of its choosing -- and, through
/// the manifest, choose the digests that runtime was verified against.
///
/// That is a persistence and trust-laundering primitive rather than initial
/// access, but it defeats the entire point of a signed manifest.
///
/// `network.rs` has always done this correctly for the port overrides, with the
/// same reasoning: packaged releases use app-owned allocation and overrides
/// exist only to make source runs deterministic. This is that rule, applied to
/// the three places it was missing.
pub(crate) fn dev_override(name: &str) -> Option<std::ffi::OsString> {
    if !cfg!(debug_assertions) {
        return None;
    }
    std::env::var_os(name).filter(|value| !value.is_empty())
}

pub(crate) fn bundled_host_pack_root() -> Option<PathBuf> {
    if let Some(root) = dev_override("LEMMA_DESKTOP_HOST_PACK_ROOT") {
        let root = PathBuf::from(root);
        if root.join("release.json").is_file() {
            return Some(root);
        }
    }
    let exe = std::env::current_exe().ok()?;
    let bin_dir = exe.parent()?;
    let candidates = if cfg!(target_os = "macos") {
        vec![
            bin_dir.join("../Resources/local-runtime"),
            bin_dir.join("local-runtime"),
        ]
    } else {
        vec![bin_dir.join("local-runtime")]
    };
    candidates
        .into_iter()
        .find(|root| root.join("release.json").is_file())
}

pub(crate) fn runtime_from_config_value(
    installed: &Value,
) -> Option<artifact_install::InstalledRuntime> {
    let release = installed.get("release")?.as_str()?;
    let root = PathBuf::from(installed.get("root")?.as_str()?);
    let runtime = artifact_install::installed_runtime(&root, release);
    runtime.is_complete().then_some(runtime)
}

pub(crate) fn configured_runtime(
    config: &Value,
    key: &str,
) -> Option<artifact_install::InstalledRuntime> {
    runtime_from_config_value(config.get(key)?)
}

pub(crate) fn host_pack_root() -> Option<PathBuf> {
    let config = read_config();
    bundled_host_pack_root().or_else(|| {
        configured_runtime(&config, "installedRuntime").map(|runtime| runtime.host_pack_root)
    })
}

pub(crate) fn bundled_managed_runtime_root() -> Option<PathBuf> {
    if let Some(root) = dev_override("LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT") {
        let root = PathBuf::from(root);
        if managed_runtime_marker(&root).is_file() {
            return Some(root);
        }
    }
    let exe = std::env::current_exe().ok()?;
    let bin_dir = exe.parent()?;
    let candidates = if cfg!(target_os = "macos") {
        vec![
            bin_dir.join("../Resources/managed-runtime"),
            bin_dir.join("managed-runtime"),
        ]
    } else {
        vec![bin_dir.join("managed-runtime")]
    };
    candidates
        .into_iter()
        .find(|root| managed_runtime_marker(root).is_file())
}

pub(crate) fn managed_runtime_root() -> Option<PathBuf> {
    let config = read_config();
    bundled_managed_runtime_root().or_else(|| {
        configured_runtime(&config, "installedRuntime").map(|runtime| runtime.managed_runtime_root)
    })
}

pub(crate) fn bundled_release_manifest() -> Option<PathBuf> {
    if let Some(path) = dev_override("LEMMA_DESKTOP_RELEASE_MANIFEST") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Some(path);
        }
    }
    let executable = std::env::current_exe().ok()?;
    let bin_dir = executable.parent()?;
    let candidates = if cfg!(target_os = "macos") {
        vec![
            bin_dir.join("../Resources/lemma-local.json"),
            bin_dir.join("../Resources/runtime/lemma-local.json"),
            bin_dir.join("lemma-local.json"),
        ]
    } else {
        vec![
            bin_dir.join("lemma-local.json"),
            bin_dir.join("runtime/lemma-local.json"),
        ]
    };
    candidates.into_iter().find(|path| path.is_file())
}

pub(crate) fn managed_runtime_marker(root: &std::path::Path) -> PathBuf {
    root.join(if cfg!(target_os = "macos") {
        "macos-aarch64/runtime.json"
    } else {
        "windows-x86_64/runtime.json"
    })
}

pub(crate) fn bundled_sibling(name: &str) -> Option<PathBuf> {
    let executable = std::env::current_exe().ok()?;
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let candidate = executable.parent()?.join(format!("{name}{suffix}"));
    candidate.is_file().then_some(candidate)
}

#[cfg(target_os = "macos")]
pub(crate) fn bundled_vz() -> Option<PathBuf> {
    // `dev_override` for the reason `locald_binary` gives: this names the
    // helper that owns the virtual machine.
    if let Some(path) = dev_override("LEMMA_DESKTOP_VZ_BIN")
        .map(PathBuf::from)
        .filter(|path| path.is_file())
    {
        return Some(path);
    }
    let executable = std::env::current_exe().ok()?;
    let bin_dir = executable.parent()?;
    [
        // Signed resource in packaged apps. It is deliberately not an
        // externalBin because Tauri would replace its helper entitlement.
        bin_dir.join("../Resources/lemma-vz"),
        // Development/test compatibility.
        bin_dir.join("lemma-vz"),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

pub(crate) fn host_pack_release(root: &std::path::Path) -> Option<String> {
    let release = root.join("release.json");
    let payload: Value = serde_json::from_slice(&std::fs::read(release).ok()?).ok()?;
    payload["version"].as_str().map(str::to_owned)
}

pub(crate) fn runtime_info_snapshot() -> RuntimeInfo {
    let config = read_config();
    let configured = configured_runtime(&config, "installedRuntime");
    let previous = configured_runtime(&config, "previousRuntime");
    let bundled = bundled_host_pack_root()
        .filter(|_| bundled_managed_runtime_root().is_some())
        .and_then(|root| host_pack_release(&root));
    let (active_release, source) = if bundled.is_some() {
        (bundled, "bundled".to_string())
    } else {
        (
            configured.as_ref().map(|runtime| runtime.release.clone()),
            "downloaded".to_string(),
        )
    };
    let downloaded_active = source == "downloaded";
    RuntimeInfo {
        desktop_release: env!("CARGO_PKG_VERSION").into(),
        active_release,
        previous_release: previous.as_ref().map(|runtime| runtime.release.clone()),
        source,
        // Schema-1 releases do not declare database rollback compatibility.
        // Retain the prior immutable pack, but never offer an unsafe downgrade.
        rollback_available: false,
        repair_available: downloaded_active
            && config
                .pointer("/installedRuntime/release")
                .and_then(Value::as_str)
                == Some(env!("CARGO_PKG_VERSION")),
    }
}

/// Whether the daemon on the socket is the one this build ships.
///
/// Version and API revision are not enough: replacing an installed app leaves
/// the previous bundle's daemon running — from `~/.Trash`, once macOS has moved
/// it — holding the control socket, reporting the same `0.7.0` and the same
/// revision, and supervising the *previous* release's runtime. The hosted path
/// used to accept whatever was listening, so a new app adopted that daemon,
/// sent it Agent Host commands it answered from stale state, and started a
/// local stack out of the old host pack that could never come up.
///
/// A daemon that does not say which binary it is answers this with `false`,
/// which is the right answer: every build that does not report it predates the
/// field, and is therefore not this one.
pub(crate) fn locald_is_this_build(
    hello: &Value,
    expected_executable: Option<&std::path::Path>,
) -> bool {
    if hello["daemon_api_revision"].as_u64() != Some(REQUIRED_LOCALD_API_REVISION) {
        return false;
    }
    let Some(expected) = expected_executable else {
        // No packaged sidecar to compare against — a source checkout running
        // whatever it built. Identity is the developer's business there.
        return true;
    };
    hello["executable"].as_str() == Some(path_identity(expected).as_str())
}

pub(crate) fn locald_matches_host_pack(
    hello: &Value,
    required_release: Option<&str>,
    required_root: Option<&std::path::Path>,
) -> bool {
    match (required_release, required_root) {
        (None, None) => true,
        (Some(release), Some(root)) => {
            matches!(hello["mode"].as_str(), Some("host-packs" | "managed-local"))
                && hello["daemon_api_revision"].as_u64() == Some(REQUIRED_LOCALD_API_REVISION)
                && hello["host_pack_release"].as_str() == Some(release)
                && hello["host_pack_root"].as_str() == Some(path_identity(root).as_str())
        }
        _ => false,
    }
}

pub(crate) fn enriched_path() -> String {
    // Only the unix arm below appends to this.
    #[cfg_attr(not(unix), expect(unused_mut, reason = "extended on unix only"))]
    let mut parts: Vec<PathBuf> = std::env::var_os("PATH")
        .map(|value| std::env::split_paths(&value).collect())
        .unwrap_or_default();
    #[cfg(unix)]
    {
        for extra in [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
        ] {
            let extra = PathBuf::from(extra);
            if !parts.contains(&extra) {
                parts.push(extra);
            }
        }
    }
    std::env::join_paths(parts)
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}

// ---------------------------------------------------------------------------
// Durable local daemon lifecycle
// ---------------------------------------------------------------------------

/// The daemon binary this build starts.
///
/// Shared with the identity check rather than duplicated: comparing a running
/// daemon against a *different* resolution than the one that spawns it is how a
/// dev run — where `LEMMA_DESKTOP_LOCALD_BIN` overrides the sidecar — would
/// reject the very daemon it had just started, forever.
pub(crate) fn locald_binary() -> Option<PathBuf> {
    // Through `dev_override`, which is inert outside a development build. This
    // read the environment directly, which made a signed, notarized Lemma
    // start whichever `lemma-locald` its environment named -- a worse version
    // of the redirection the three manifest variables are already gated
    // against, because this one is the executable itself. Only
    // `scripts/dev-local.sh` sets it.
    dev_override("LEMMA_DESKTOP_LOCALD_BIN")
        .map(PathBuf::from)
        .filter(|p| p.exists())
        .or_else(bundled_locald)
        .or_else(|| {
            // One workspace under desktop/, so one target directory.
            let candidate = runtime_root().join(if cfg!(windows) {
                "desktop/target/debug/lemma-locald.exe"
            } else {
                "desktop/target/debug/lemma-locald"
            });
            candidate.exists().then_some(candidate)
        })
}

/// Spawn a child without flashing up a console window.
///
/// A release build sets windows_subsystem to windows, so the app has no
/// console at all. lemma-locald.exe is a console program, and starting one
/// from a process with no console makes Windows allocate a visible conhost
/// window for it -- one the user can close, which kills the daemon.
/// Redirecting stdio does not suppress it.
///
/// A no-op everywhere else, so call sites stay platform-neutral.
pub(crate) trait NoConsoleWindow {
    fn no_console_window(&mut self) -> &mut Self;
}

#[tauri::command(async)]
pub(crate) fn runtime_info(window: Webview) -> Result<RuntimeInfo, String> {
    require_control_window(&window)?;
    Ok(runtime_info_snapshot())
}
