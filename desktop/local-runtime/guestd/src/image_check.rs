//! Whether a sandbox image is really unpacked, and how often that is asked.
//!
//! An interrupted VM shutdown can leave containerd's metadata saying an image
//! is present while its unpacked snapshot is not. `ensure_sandbox_image`
//! (`images.rs`) runs this check before a sandbox starts and repairs only on
//! the answer "incomplete".

use super::*;

/// What `sandbox_image_check` found.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ImageCheck {
    Ready,
    /// The runtime entrypoint is missing from the unpacked image.
    Incomplete,
    /// The check itself could not run; nothing is known about the image.
    Unknown(String),
}

/// The check's own exit status for "the marker file is missing": one no
/// engine uses for its own failures.
pub(crate) const MARKER_MISSING: i32 = 3;

/// Whether the registry `image` names answers at all, over HTTPS. Any HTTP
/// status counts -- an unauthenticated `/v2/` answers 401 -- since the
/// question is only whether a pull could reach it.
pub(crate) fn image_registry_reachable(image: &str) -> bool {
    let first = image.split('/').next().unwrap_or_default();
    let host = if image.contains('/') && (first.contains('.') || first.contains(':')) {
        first
    } else {
        "registry-1.docker.io"
    };
    Command::new("/usr/bin/curl")
        .args([
            "--silent",
            "--output",
            "/dev/null",
            "--connect-timeout",
            "3",
            "--max-time",
            "5",
            "--write-out",
            "%{http_code}",
        ])
        .arg(format!("https://{host}/v2/"))
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .is_ok_and(|output| {
            let code = String::from_utf8_lossy(&output.stdout);
            code.trim() != "000" && !code.trim().is_empty()
        })
}

impl<E: Engine + 'static> GuestService<E> {
    /// Running containers made from `image`.
    pub(crate) fn running_containers_from(&self, image: &str) -> Result<Vec<String>, GuestError> {
        let output = self.run_checked(&[
            "ps".into(),
            "--quiet".into(),
            "--filter".into(),
            format!("ancestor={image}"),
        ])?;
        parse_container_ids(&output)
    }

    /// Where a passed check is recorded, keyed by image and workload.
    pub(crate) fn image_check_record(&self, image: &str, workload_kind: WorkloadKind) -> PathBuf {
        let key = format!("{image}\n{workload_kind:?}");
        let hash = key.bytes().fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
            (hash ^ u64::from(byte)).wrapping_mul(0x0100_0000_01b3)
        });
        self.state_root
            .join("run/image-checked")
            .join(format!("{hash:016x}"))
    }

    /// Whether this image passed its check since the guest last booted.
    ///
    /// The check starts a container, under the lock every mutation holds, on
    /// every sandbox created -- a second or more each time, for a question
    /// whose answer only changes when a shutdown is interrupted. So it is
    /// asked once per image per boot.
    pub(crate) fn image_checked_this_boot(&self, image: &str, workload_kind: WorkloadKind) -> bool {
        let Some(boot) = &self.boot_id else {
            return false;
        };
        fs::read_to_string(self.image_check_record(image, workload_kind))
            .is_ok_and(|recorded| recorded.trim() == boot)
    }

    pub(crate) fn record_image_checked(&self, image: &str, workload_kind: WorkloadKind) {
        let Some(boot) = &self.boot_id else {
            return;
        };
        let path = self.image_check_record(image, workload_kind);
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        // Best effort: without it the check simply runs again next time.
        let _ = fs::write(path, boot);
    }

    #[cfg(test)]
    pub(crate) fn sandbox_image_marker_is_ready(
        &self,
        image: &str,
        workload_kind: WorkloadKind,
    ) -> bool {
        self.sandbox_image_check(image, workload_kind) == ImageCheck::Ready
    }

    /// Whether the image's runtime entrypoint is unpacked, by running it.
    ///
    /// The answer distinguishes "the file is not there" from "the engine
    /// could not run the check": only the first is a reason to repair. A
    /// missing file exits `MARKER_MISSING`; a snapshot so incomplete the
    /// shell itself is gone fails to exec, which the engine reports with
    /// status 126/127 or an exec error.
    pub(crate) fn sandbox_image_check(
        &self,
        image: &str,
        workload_kind: WorkloadKind,
    ) -> ImageCheck {
        let marker = match workload_kind {
            WorkloadKind::Workspace => "/usr/local/bin/start-workspace-runtime",
            WorkloadKind::Function => "/usr/local/bin/lemma-function-runtime",
        };
        let output = match self.engine.run(&[
            "run".into(),
            "--rm".into(),
            "--network".into(),
            "none".into(),
            "--platform".into(),
            guest_platform().into(),
            image.into(),
            "/bin/sh".into(),
            "-c".into(),
            format!("test -s \"$1\" || exit {MARKER_MISSING}"),
            "lemma-image-check".into(),
            marker.into(),
        ]) {
            Ok(output) => output,
            Err(error) => return ImageCheck::Unknown(error),
        };
        if output.status.success() {
            return ImageCheck::Ready;
        }
        let stderr = String::from_utf8_lossy(&output.stderr);
        match output.status.code() {
            Some(code) if code == MARKER_MISSING || code == 126 || code == 127 => {
                ImageCheck::Incomplete
            }
            _ if stderr.contains("no such file or directory")
                || stderr.contains("executable file not found") =>
            {
                ImageCheck::Incomplete
            }
            _ => ImageCheck::Unknown(redact_engine_error(stderr.trim())),
        }
    }
}
