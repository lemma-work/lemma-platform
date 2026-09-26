//! Giving disk back: container images nothing will run again, and the blocks
//! ext4 has freed but the host file still holds.
//!
//! Every release pins new sandbox and service images by digest, and nothing
//! removed the old ones. Each update left several hundred megabytes of images
//! on the data disk that no container could ever start from again, for the
//! life of the installation. `core.prune_images` removes them; `core.trim`
//! hands the freed blocks back to macOS, where `data.raw` is a sparse file.

use super::*;
use std::collections::HashSet;

/// Where the data disk is mounted on the macOS guest (`lemma-mount-data`).
pub(crate) const DATA_DISK_MOUNT: &str = "/var/lib/lemma-data";
const FSTRIM: &str = "/usr/sbin/fstrim";
const TRIM_TIMEOUT: Duration = Duration::from_secs(5 * 60);

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PruneParameters {
    /// The running release's images: never removed, whether or not anything
    /// runs from them right now.
    pub(crate) images: CoreImages,
}

/// One image the engine holds, by the name it is stored under.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct StoredImage {
    pub(crate) name: String,
    /// The manifest digest, when the engine reports one.
    pub(crate) digest: Option<String>,
}

/// A reference as the engine stores it: `postgres@sha256:x` is
/// `docker.io/library/postgres@sha256:x`, and a missing tag is `latest`.
pub(crate) fn normalize_reference(reference: &str) -> String {
    let reference = reference.trim();
    let (name, suffix) = match reference.find('@') {
        Some(at) => (&reference[..at], &reference[at..]),
        None => {
            // A tag is the part after the last colon, when that colon is past
            // the last slash -- a registry port is not a tag.
            let slash = reference.rfind('/').map_or(0, |index| index + 1);
            match reference[slash..].rfind(':') {
                Some(colon) => (&reference[..slash + colon], &reference[slash + colon..]),
                None => (reference, ":latest"),
            }
        }
    };
    let first = name.split('/').next().unwrap_or_default();
    let has_domain =
        name.contains('/') && (first.contains('.') || first.contains(':') || first == "localhost");
    let qualified = if has_domain {
        name.to_owned()
    } else if name.contains('/') {
        format!("docker.io/{name}")
    } else {
        format!("docker.io/library/{name}")
    };
    format!("{qualified}{suffix}")
}

/// The `sha256:` digest a reference pins, if it pins one.
fn pinned_digest(reference: &str) -> Option<&str> {
    reference.split_once('@').map(|(_, digest)| digest)
}

/// Which stored images to remove. Pure, so the rule is the thing tested.
///
/// An image stays when a release that is running names it -- by name, or by
/// the digest it pins -- or when any container, running or stopped, was made
/// from it: a stopped sandbox starts again from its image. Everything else is
/// an image no container on this machine can reach, and the next
/// `sandbox.ensure` that wants it pulls it again.
pub(crate) fn images_to_prune(
    stored: &[StoredImage],
    keep: &[String],
    in_use: &[String],
) -> Vec<String> {
    let protected: HashSet<String> = keep
        .iter()
        .chain(in_use)
        .map(|reference| normalize_reference(reference))
        .collect();
    let protected_digests: HashSet<&str> = keep
        .iter()
        .chain(in_use)
        .filter_map(|reference| pinned_digest(reference))
        .collect();
    stored
        .iter()
        .filter(|image| {
            let name = image.name.trim();
            // Unnamed (dangling) entries cannot be told apart safely by name.
            if name.is_empty() || name.contains("<none>") {
                return false;
            }
            if protected.contains(&normalize_reference(name)) {
                return false;
            }
            let digests = [pinned_digest(name), image.digest.as_deref()];
            !digests
                .into_iter()
                .flatten()
                .any(|digest| protected_digests.contains(digest))
        })
        .map(|image| image.name.clone())
        .collect()
}

/// One line of `nerdctl images --format '{{json .}}'`.
///
/// `Name` where this nerdctl reports it; otherwise rebuilt from repository
/// and tag, or repository and digest for an image pulled by digest.
pub(crate) fn parse_stored_images(listing: &str) -> Result<Vec<StoredImage>, GuestError> {
    let mut images = Vec::new();
    for line in listing.lines().filter(|line| !line.trim().is_empty()) {
        let row: Value = serde_json::from_str(line)
            .map_err(|_| GuestError::engine("container engine listed an unreadable image"))?;
        let field = |key: &str| {
            row.get(key)
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty() && *value != "<none>")
                .map(str::to_owned)
        };
        let digest = field("Digest");
        let name = field("Name").or_else(|| {
            let repository = field("Repository")?;
            match (field("Tag"), digest.as_deref()) {
                (Some(tag), _) => Some(format!("{repository}:{tag}")),
                (None, Some(digest)) => Some(format!("{repository}@{digest}")),
                (None, None) => None,
            }
        });
        if let Some(name) = name {
            images.push(StoredImage { name, digest });
        }
    }
    Ok(images)
}

impl<E: Engine + 'static> GuestService<E> {
    /// Remove the images no container uses and no running release names.
    pub(crate) fn prune_images(&self, parameters: Value) -> Result<Value, GuestError> {
        let parameters: PruneParameters = serde_json::from_value(parameters)
            .map_err(|error| GuestError::invalid(format!("invalid prune parameters: {error}")))?;
        let images = parameters.images;
        let keep: Vec<String> = [
            Some(images.postgres),
            Some(images.redis),
            Some(images.supertokens),
            images.workspace,
            images.function,
        ]
        .into_iter()
        .flatten()
        .collect();
        // Every container, stopped ones included. A listing that fails ends
        // the prune: not knowing what is in use is not "nothing is".
        let in_use: Vec<String> = self
            .run_checked(&[
                "ps".into(),
                "--all".into(),
                "--no-trunc".into(),
                "--format".into(),
                "{{.Image}}".into(),
            ])?
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(str::to_owned)
            .collect();
        let stored = parse_stored_images(&self.run_checked(&[
            "images".into(),
            "--no-trunc".into(),
            "--format".into(),
            "{{json .}}".into(),
        ])?)?;
        let mut removed = Vec::new();
        let mut failed = Vec::new();
        // An image being fetched right now is about to be used, and is half
        // in the store: never a candidate, by this process's table or by the
        // claim another guestd process holds while it pulls.
        let arriving: HashSet<String> = in_flight_pulls()
            .lock()
            .expect("pull table poisoned")
            .iter()
            .filter(|(_, state)| matches!(state, PullState::Running))
            .map(|(image, _)| normalize_reference(image))
            .collect();
        let claims = self.pull_claims();
        for name in images_to_prune(&stored, &keep, &in_use) {
            if arriving.contains(&normalize_reference(&name)) {
                continue;
            }
            let Ok(Some(_claim)) = claim_pull(&claims, &name) else {
                continue;
            };
            // Never `--force`: the engine's own refusal for an image a
            // container still uses is a second guard behind the one above.
            let output = self
                .engine
                .run(&["rmi".into(), name.clone()])
                .map_err(GuestError::engine)?;
            if output.status.success() {
                removed.push(name);
            } else {
                failed.push(name);
            }
        }
        Ok(json!({"removed": removed, "kept_in_use": in_use.len(), "failed": failed}))
    }

    /// Hand the data disk's free blocks back to the host.
    ///
    /// The disk is mounted with `discard`, so most deletes already do this as
    /// they happen; `fstrim` catches what online discard skips (small extents,
    /// and anything freed before the option was on). A device that does not
    /// accept discards answers `supported: false` rather than an error.
    pub(crate) fn trim_data_disk(&self) -> Result<Value, GuestError> {
        trim_mount(
            Path::new(FSTRIM),
            Path::new(DATA_DISK_MOUNT),
            &self.state_root,
        )
    }
}

pub(crate) fn trim_mount(
    fstrim: &Path,
    mount: &Path,
    state_root: &Path,
) -> Result<Value, GuestError> {
    if !fstrim.is_file() || !mount.is_dir() {
        return Ok(json!({"supported": false, "detail": "no data disk to trim here"}));
    }
    let capture = state_root.join("run/engine-tmp");
    let _ = fs::create_dir_all(&capture);
    let output = run_bounded_engine_command(
        fstrim,
        &capture,
        &["--verbose".into(), mount.to_string_lossy().into_owned()],
        TRIM_TIMEOUT,
    )
    .map_err(GuestError::engine)?;
    Ok(trim_outcome(
        output.status.success(),
        &String::from_utf8_lossy(&output.stdout),
        &String::from_utf8_lossy(&output.stderr),
    ))
}

/// What `fstrim --verbose` said, as an answer.
///
/// `/var/lib/lemma-data: 1.2 GiB (1288490188 bytes) trimmed on /dev/nvme0n1`
/// on success; "the discard operation is not supported" when the device
/// does not take discards.
pub(crate) fn trim_outcome(success: bool, stdout: &str, stderr: &str) -> Value {
    if success {
        let trimmed = stdout.split('(').nth(1).and_then(|rest| {
            rest.split_whitespace()
                .next()
                .and_then(|bytes| bytes.parse::<u64>().ok())
        });
        return json!({"supported": true, "trimmed_bytes": trimmed});
    }
    let detail = stderr.trim();
    if detail.contains("not supported") {
        return json!({"supported": false, "detail": "the data disk does not accept discards"});
    }
    json!({"supported": true, "trimmed_bytes": null, "detail": redact_engine_error(detail)})
}
