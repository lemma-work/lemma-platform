//! Which guest image is installed, and whether it is the one this build
//! ships.

use super::*;

/// Which rootfs a registered distribution was imported from.
///
/// The question this answers is "was this guest imported from a *different
/// release*", and the answer decides whether the runtime distribution is
/// replaced. Getting it wrong in either direction is expensive, and the two
/// directions cost different things.
///
/// Not modification time. That changes whenever the archive is written again,
/// so a repair, a re-download or a plain reinstall of the very same release
/// all looked like a different one -- and back when the data lived inside the
/// distribution, that offered to destroy the only copy of it over a timestamp.
///
/// The recorded digest when there is one. `artifact_install` writes the signed
/// manifest's `guest_sha256` beside the release when it installs it, so the
/// exact identity is already on disk and costs a small read -- not the several
/// gigabytes of hashing that made "just hash it" the wrong answer here.
///
/// Size only when there is not, which is the older layout and the bundled one.
/// It is a weak discriminator and worth saying why: `tar` pads every member to
/// a 512-byte block and the archive to the blocking factor, so archive size is
/// coarsely quantised and two builds that differ by a few bytes in a script --
/// which is exactly what a guest-side change usually is -- routinely produce a
/// byte-identical size. A false "current" leaves this release's host talking to
/// the previous release's guestd, which is the failure this check exists for.
///
/// Free and un-gated so it is tested on every platform, not only compiled on
/// one -- the Windows guest path is its only caller, and code that exists on
/// one platform and is checked on none is how the mtime bug survived.
#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn rootfs_stamp(rootfs: &Path) -> io::Result<String> {
    if let Some(digest) = recorded_guest_digest(rootfs) {
        return Ok(digest);
    }
    Ok(fs::metadata(rootfs)?.len().to_string())
}

/// The guest artifact's digest, as the installer recorded it.
///
/// `<release>/managed-runtime/<target>/rootfs.tar` is three levels below the
/// release root, which is where `.lemma-runtime-artifacts.json` lives. Absent,
/// unreadable or missing the field all mean the same thing -- there is nothing
/// better than size to go on -- so none of them is an error.
#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn recorded_guest_digest(rootfs: &Path) -> Option<String> {
    let recorded = rootfs
        .ancestors()
        .nth(3)?
        .join(".lemma-runtime-artifacts.json");
    let value: Value = serde_json::from_slice(&fs::read(recorded).ok()?).ok()?;
    let digest = value.get("guest_sha256")?.as_str()?.trim();
    (!digest.is_empty()).then(|| digest.to_owned())
}

#[cfg(target_os = "macos")]
pub(crate) fn validate_macos_release(source: &Path) -> io::Result<()> {
    let source_marker = source.join("runtime.json");
    if !source_marker.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "managed runtime metadata is missing: {}",
                source_marker.display()
            ),
        ));
    }
    for name in ["vmlinuz", "initrd", "disk.raw"] {
        let path = source.join(name);
        if !path.is_file() || path.metadata()?.len() == 0 {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("managed runtime artifact is missing: {}", path.display()),
            ));
        }
    }
    let metadata: Value = serde_json::from_slice(&fs::read(&source_marker)?)?;
    if metadata
        .get("service_transport_version")
        .and_then(Value::as_u64)
        != Some(1)
    {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "This local runtime does not support the app's private service transport. Install the matching runtime update, then retry. Your stored data has not been changed.",
        ));
    }
    Ok(())
}

impl ManagedRuntime {
    /// Where the identity of the imported guest is recorded.
    #[cfg(windows)]
    pub(crate) fn guest_release_marker(&self) -> PathBuf {
        self.config
            .local_root
            .join("runtime/wsl/.lemma-guest-release")
    }

    /// Whether the imported guest came from this release's rootfs.
    ///
    /// The distribution's name says nothing about which release built it, so
    /// an upgrade used to skip the import and leave the new host talking to
    /// the old guestd. Every guest operation then failed as "could not reach
    /// Lemma's private runtime", with nothing pointing at the cause.
    #[cfg(windows)]
    pub(crate) fn installed_guest_is_current(&self, rootfs: &Path) -> io::Result<bool> {
        if !rootfs.is_file() {
            // Nothing to compare against. An installed guest with no artifact
            // is the repair path's problem, not this one's.
            return Ok(true);
        }
        let marker = self.guest_release_marker();
        let recorded = fs::read_to_string(&marker).unwrap_or_default();
        let current = rootfs_stamp(rootfs)?;
        if recorded == current {
            return Ok(true);
        }
        if recorded.is_empty() {
            // A distribution imported before this marker existed. Adopt it
            // rather than declaring every existing installation broken.
            fs::write(&marker, &current)?;
            return Ok(true);
        }
        Ok(false)
    }
}
