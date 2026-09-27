//! Reusing a component an earlier release already installed.
//!
//! A release is two archives, and they change at very different rates: the
//! host pack carries the app and changes every release, the guest runtime is a
//! Linux image that changes rarely. Installing per release meant every update
//! downloaded the guest again although the signed manifest named the very
//! archive already expanded on this disk.
//!
//! So a component whose archive digest matches one an installed release was
//! built from is copied from that release instead of downloaded. The copy is
//! trusted for exactly the reason a download is: it is hashed, and the hash has
//! to match. What it is compared with is the digest of the expanded tree,
//! recorded when that tree was extracted from a verified archive -- the archive
//! itself is deleted after installation, so the tree is what there is to check.
//! A copy that does not match is discarded and the archive is downloaded as if
//! there had been nothing to reuse.

use super::*;

pub(crate) const INSTALLED_CONTENTS_FILE: &str = ".lemma-runtime-contents.json";
const CONTENTS_SCHEMA_VERSION: u64 = 1;

/// One of the two archives a release is made of.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Component {
    Host,
    Guest,
}

impl Component {
    /// The directory under a release root that this component's archive
    /// expands into, and that nothing else writes to.
    pub(crate) fn tree(self) -> &'static str {
        match self {
            Component::Host => "local-runtime",
            Component::Guest => "managed-runtime",
        }
    }

    pub(crate) fn name(self) -> &'static str {
        match self {
            Component::Host => "host",
            Component::Guest => "guest",
        }
    }

    fn recorded(self, identity: &InstalledArtifactIdentity) -> (&str, &str, u64) {
        match self {
            Component::Host => (
                &identity.host_target,
                &identity.host_sha256,
                identity.host_size,
            ),
            Component::Guest => (
                &identity.guest_target,
                &identity.guest_sha256,
                identity.guest_size,
            ),
        }
    }

    fn target(self) -> &'static str {
        match self {
            Component::Host => host_target(),
            Component::Guest => guest_target(),
        }
    }
}

/// What each component of an installed release expanded to.
///
/// Its own file rather than more fields on the artifact identity: that record
/// is compared for equality and refuses unknown fields, and an older Lemma
/// reading a release this one installed must still recognise it.
#[derive(Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct InstalledContents {
    schema_version: u64,
    #[serde(default)]
    host: Option<ComponentContents>,
    #[serde(default)]
    guest: Option<ComponentContents>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ComponentContents {
    /// The archive the tree was extracted from, as the signed manifest named it.
    pub(crate) archive_sha256: String,
    /// [`tree_digest`] of the tree, taken right after it was installed.
    pub(crate) tree_sha256: String,
}

impl InstalledContents {
    pub(crate) fn new() -> Self {
        Self {
            schema_version: CONTENTS_SCHEMA_VERSION,
            ..Self::default()
        }
    }

    pub(crate) fn set(&mut self, component: Component, contents: ComponentContents) {
        match component {
            Component::Host => self.host = Some(contents),
            Component::Guest => self.guest = Some(contents),
        }
    }

    pub(crate) fn get(&self, component: Component) -> Option<&ComponentContents> {
        match component {
            Component::Host => self.host.as_ref(),
            Component::Guest => self.guest.as_ref(),
        }
    }
}

pub(crate) fn read_installed_contents(root: &Path) -> Option<InstalledContents> {
    let contents: InstalledContents =
        serde_json::from_slice(&fs::read(root.join(INSTALLED_CONTENTS_FILE)).ok()?).ok()?;
    (contents.schema_version == CONTENTS_SCHEMA_VERSION).then_some(contents)
}

pub(crate) fn write_installed_contents(
    root: &Path,
    contents: &InstalledContents,
) -> io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(root.join(INSTALLED_CONTENTS_FILE))?;
    file.write_all(&serde_json::to_vec(contents)?)?;
    file.write_all(b"\n")?;
    file.sync_all()
}

/// A digest of everything under `root`: every path, its kind, and every
/// file's permission bits where the platform has them, size and SHA-256.
///
/// Paths are joined with `/` from their components, so the digest does not
/// depend on the platform's separator. A symbolic link is refused rather than
/// followed: no archive this installs may contain one, so a tree that has one
/// is not a tree it installed. Returns the digest and the total file bytes.
pub(crate) fn tree_digest(root: &Path, progress: &mut dyn FnMut(u64)) -> io::Result<(String, u64)> {
    let mut lines = Vec::new();
    let mut total = 0_u64;
    walk_tree(root, "", &mut lines, &mut total, progress)?;
    lines.sort();
    let mut digest = Sha256::new();
    for line in lines {
        digest.update(line.as_bytes());
        digest.update(b"\n");
    }
    Ok((hex::encode(digest.finalize()), total))
}

fn walk_tree(
    directory: &Path,
    prefix: &str,
    lines: &mut Vec<String>,
    total: &mut u64,
    progress: &mut dyn FnMut(u64),
) -> io::Result<()> {
    let mut entries = fs::read_dir(directory)?.collect::<io::Result<Vec<_>>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("runtime tree has a path that is not UTF-8"))?;
        let relative = if prefix.is_empty() {
            name
        } else {
            format!("{prefix}/{name}")
        };
        let metadata = fs::symlink_metadata(entry.path())?;
        if metadata.is_dir() {
            lines.push(format!("d {relative}"));
            walk_tree(&entry.path(), &relative, lines, total, progress)?;
        } else if metadata.is_file() {
            let sha256 = file_sha256(&entry.path())?;
            *total = total
                .checked_add(metadata.len())
                .ok_or_else(|| invalid("runtime tree size overflow"))?;
            progress(*total);
            lines.push(format!(
                "f {:o} {} {sha256} {relative}",
                permission_bits(&metadata),
                metadata.len()
            ));
        } else {
            return Err(invalid(format!(
                "runtime tree contains something that is not a file or directory: {relative}"
            )));
        }
    }
    Ok(())
}

#[cfg(unix)]
fn permission_bits(metadata: &fs::Metadata) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    metadata.permissions().mode() & 0o777
}

#[cfg(not(unix))]
fn permission_bits(metadata: &fs::Metadata) -> u32 {
    u32::from(metadata.permissions().readonly())
}

/// Record what this staged release's components expanded to.
///
/// `reused` carries the digests a reuse already computed, so those trees are
/// not read twice. A host pack whose archive put anything beside
/// `local-runtime` is left unrecorded: the tree would not be the whole of what
/// the archive installed, and a later reuse of it would be missing a part.
pub(crate) fn record_installed_contents(
    staging: &Path,
    host: &ArtifactRef,
    guest: &ArtifactRef,
    reused: &HashMap<&'static str, String>,
) -> io::Result<()> {
    let mut contents = InstalledContents::new();
    for (component, artifact) in [(Component::Host, host), (Component::Guest, guest)] {
        if component == Component::Host && !only_component_trees(staging)? {
            continue;
        }
        let tree_sha256 = match reused.get(component.name()) {
            Some(digest) => digest.clone(),
            None => tree_digest(&staging.join(component.tree()), &mut |_| {})?.0,
        };
        contents.set(
            component,
            ComponentContents {
                archive_sha256: artifact.sha256.clone(),
                tree_sha256,
            },
        );
    }
    write_installed_contents(staging, &contents)
}

fn only_component_trees(staging: &Path) -> io::Result<bool> {
    for entry in fs::read_dir(staging)? {
        let name = entry?.file_name();
        if name != Component::Host.tree()
            && name != Component::Guest.tree()
            && name != INSTALLED_ARTIFACTS_FILE
        {
            return Ok(false);
        }
    }
    Ok(true)
}

/// An installed release that says it holds this exact component.
#[derive(Debug)]
pub(crate) struct ReuseSource {
    pub(crate) tree: PathBuf,
    pub(crate) tree_sha256: String,
}

/// Every installed release whose records say it was built from this archive.
///
/// Reads two small files per release and nothing else, so it is cheap enough
/// to ask before an update is even downloaded. The answer is a claim: the
/// install still hashes whatever it copies. Staging directories are skipped,
/// as is anything without both records -- which is every release a Lemma from
/// before these records installed, and those simply download as they did.
pub(crate) fn find_reusable(
    install_root: &Path,
    component: Component,
    sha256: &str,
    size: u64,
) -> Vec<ReuseSource> {
    let Ok(entries) = fs::read_dir(install_root.join("releases")) else {
        return Vec::new();
    };
    let mut roots: Vec<PathBuf> = entries
        .flatten()
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| !name.starts_with('.'))
        })
        .collect();
    roots.sort();
    roots
        .into_iter()
        .filter_map(|root| {
            let identity = read_installed_artifacts(&root)?;
            let (target, recorded_sha256, recorded_size) = component.recorded(&identity);
            if identity.schema_version != MANIFEST_SCHEMA_VERSION
                || target != component.target()
                || recorded_sha256 != sha256
                || recorded_size != size
                || !valid_recorded_digest(sha256)
            {
                return None;
            }
            let contents = read_installed_contents(&root)?;
            let recorded = contents.get(component)?;
            if recorded.archive_sha256 != sha256 || !valid_recorded_digest(&recorded.tree_sha256) {
                return None;
            }
            let tree = root.join(component.tree());
            tree.is_dir().then(|| ReuseSource {
                tree,
                tree_sha256: recorded.tree_sha256.clone(),
            })
        })
        .collect()
}

/// Copy a verified installed component into `staging`, or report that there
/// was none and the archive has to be downloaded.
///
/// Returns the tree digest when a copy verified. Every failure -- nothing to
/// reuse, a copy that could not be made, a copy whose digest did not match --
/// leaves `staging` without the component, which is what makes the caller's
/// fallback a plain download. The reason is reported through `progress`, which
/// is what puts it in the install log.
pub(crate) fn reuse_installed_component(
    install_root: &Path,
    component: Component,
    artifact: &ArtifactRef,
    staging: &Path,
    progress: &mut dyn FnMut(InstallProgress<'_>),
) -> Option<String> {
    let destination = staging.join(component.tree());
    let (stage, label) = match component {
        Component::Host => (
            "host-reuse",
            "Checking the application runtime already installed",
        ),
        Component::Guest => (
            "guest-reuse",
            "Checking the private runtime already installed",
        ),
    };
    for source in find_reusable(install_root, component, &artifact.sha256, artifact.size) {
        let mut last_reported = 0_u64;
        let attempt = copy_tree(&source.tree, &destination).and_then(|()| {
            tree_digest(&destination, &mut |current| {
                if current.saturating_sub(last_reported) < 16 * 1024 * 1024 {
                    return;
                }
                last_reported = current;
                progress(InstallProgress {
                    stage,
                    component: component.name(),
                    label,
                    current: current.min(artifact.expanded_size),
                    total: artifact.expanded_size,
                    bytes: true,
                })
            })
        });
        match attempt {
            Ok((digest, bytes))
                if digest == source.tree_sha256 && bytes == artifact.expanded_size =>
            {
                return Some(digest);
            }
            outcome => {
                let reason = match outcome {
                    Ok(_) => "did not match the digest recorded when it was installed".to_owned(),
                    Err(error) => format!("could not be copied: {error}"),
                };
                progress(InstallProgress {
                    stage,
                    component: component.name(),
                    label: &format!(
                        "The installed {} runtime {reason}; downloading it instead",
                        component.name()
                    ),
                    current: 0,
                    total: 1,
                    bytes: false,
                });
                if destination.exists() {
                    // A leftover here would collide with the extraction the
                    // fallback download does next, so it is not optional.
                    if let Err(error) = fs::remove_dir_all(&destination) {
                        progress(InstallProgress {
                            stage,
                            component: component.name(),
                            label: &format!("could not discard the rejected copy: {error}"),
                            current: 0,
                            total: 1,
                            bytes: false,
                        });
                        return None;
                    }
                }
            }
        }
    }
    None
}

/// Copy a runtime tree file by file.
///
/// `fs::copy` clones on APFS, so on macOS this costs no space and no time for
/// a gigabyte disk image, and a clone is still a separate file: nothing done to
/// one release's copy reaches the other's. Elsewhere it is a real copy.
pub(crate) fn copy_tree(source: &Path, destination: &Path) -> io::Result<()> {
    fs::create_dir(destination)?;
    let mut entries = fs::read_dir(source)?.collect::<io::Result<Vec<_>>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let metadata = fs::symlink_metadata(entry.path())?;
        let target = destination.join(entry.file_name());
        if metadata.is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else if metadata.is_file() {
            fs::copy(entry.path(), &target)?;
        } else {
            return Err(invalid("runtime tree contains a link or special file"));
        }
    }
    Ok(())
}

/// How many of these archives would actually be downloaded, given what this
/// machine already has installed.
///
/// An estimate for the update dialog, not a promise: it reads records, and the
/// install hashes before it trusts any of them. A copy that turns out not to
/// verify is downloaded after all, so the estimate can only be low when the
/// disk has been damaged since.
pub(crate) fn bytes_to_download(
    install_root: &Path,
    artifacts: &[(Component, String, u64)],
) -> u64 {
    artifacts
        .iter()
        .filter(|(component, sha256, size)| {
            find_reusable(install_root, *component, sha256, *size).is_empty()
        })
        .map(|(_, _, size)| *size)
        .sum()
}
