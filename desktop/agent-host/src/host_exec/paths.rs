//! Which host paths an op may touch, and where a workspace's root is.
//!
//! The exec-server's own check, done before Seatbelt does it again
//! underneath: a denial here is a sentence the agent can act on
//! (`outside_workspace`), where a Seatbelt denial is only `EPERM`.

use std::path::{Component, Path, PathBuf};

use super::wire::{OpFailure, kind};

/// Whether `resolve` follows a symbolic link in the last component.
///
/// Reading or writing a file goes through a link, so the link's target is
/// what is checked. Deleting, moving or `stat`ing it acts on the link itself,
/// and following it would both check the wrong path and refuse to delete a
/// dangling link inside the workspace.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Leaf {
    Follow,
    NoFollow,
}

/// The folders one workspace's ops may reach: its root, the temporary
/// directory, and whatever the owner granted. All canonical.
#[derive(Clone, Debug)]
pub struct PathPolicy {
    root: PathBuf,
    allowed: Vec<PathBuf>,
}

impl PathPolicy {
    /// `root` must exist; a `tmp` or grant that does not is left out rather
    /// than failing the workspace, since nothing could be inside it anyway.
    pub fn new(root: &Path, tmp: &Path, grants: &[PathBuf]) -> std::io::Result<Self> {
        let root = std::fs::canonicalize(root)?;
        let mut allowed = vec![root.clone()];
        for extra in std::iter::once(tmp).chain(grants.iter().map(PathBuf::as_path)) {
            if let Ok(canonical) = std::fs::canonicalize(extra) {
                allowed.push(canonical);
            }
        }
        Ok(Self { root, allowed })
    }

    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// The real path `raw` names, if it is inside the policy.
    ///
    /// Relative paths are taken against the root. Symbolic links are followed
    /// everywhere except, with `Leaf::NoFollow`, the last component, and a
    /// path whose tail does not exist yet is resolved through its deepest
    /// existing ancestor -- so a link inside the root pointing outside it is
    /// caught whether or not the file behind it exists.
    pub fn resolve(&self, raw: &str, leaf: Leaf) -> Result<PathBuf, OpFailure> {
        if raw.is_empty() {
            return Err(OpFailure::invalid("a path is required"));
        }
        if raw.contains('\0') {
            return Err(OpFailure::invalid("a path may not contain NUL"));
        }
        let joined = self.root.join(raw);
        let resolved = match real_path(&joined, leaf) {
            Ok(resolved) => resolved,
            // Under Seatbelt a denied folder cannot even be looked into, so
            // resolving a path through `~/.ssh` fails before the check below
            // could say why. A path that was never inside is still outside.
            Err(failure)
                if failure.kind == kind::PERMISSION_DENIED
                    && !self
                        .allowed
                        .iter()
                        .any(|allowed| joined.starts_with(allowed)) =>
            {
                return Err(OpFailure::outside(&joined));
            }
            Err(failure) => return Err(failure),
        };
        if self
            .allowed
            .iter()
            .any(|allowed| resolved.starts_with(allowed))
        {
            Ok(resolved)
        } else {
            Err(OpFailure::outside(&resolved))
        }
    }
}

/// The canonical form of `path`, which need not exist.
fn real_path(path: &Path, leaf: Leaf) -> Result<PathBuf, OpFailure> {
    let components: Vec<Component<'_>> = path
        .components()
        .filter(|component| !matches!(component, Component::CurDir))
        .collect();
    if leaf == Leaf::NoFollow
        && let Some(Component::Normal(name)) = components.last()
    {
        let parent: PathBuf = components[..components.len() - 1].iter().collect();
        let mut resolved = real_path(&parent, Leaf::Follow)?;
        resolved.push(name);
        return Ok(resolved);
    }
    // The deepest ancestor that exists is resolved by the kernel, `..` and
    // links included. What is left below it does not exist, so it holds no
    // links; only `..` could still move it, and that is refused rather than
    // guessed at.
    for split in (1..=components.len()).rev() {
        let prefix: PathBuf = components[..split].iter().collect();
        match std::fs::canonicalize(&prefix) {
            Ok(mut resolved) => {
                for component in &components[split..] {
                    match component {
                        Component::Normal(name) => resolved.push(name),
                        _ => {
                            return Err(OpFailure::new(
                                kind::NOT_FOUND,
                                format!(
                                    "{} goes through a folder that does not exist",
                                    path.display()
                                ),
                            ));
                        }
                    }
                }
                return Ok(resolved);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            // A file where a folder is expected, or a folder we may not look
            // into: said as it is, not as a missing path.
            Err(error) => return Err(OpFailure::io(&error, &prefix)),
        }
    }
    Err(OpFailure::new(
        kind::NOT_FOUND,
        format!("{} does not exist", path.display()),
    ))
}

/// What `workspace.open` asks for, before the host decides what to trust.
#[derive(Clone, Debug, Default, serde::Deserialize)]
pub struct OpenParams {
    /// The conversation this workspace belongs to; names its default folder
    /// and is how a folder the owner bound to it is found.
    #[serde(default)]
    pub conversation_id: Option<uuid::Uuid>,
    /// A host folder to use as the root: the folder the conversation is bound
    /// to, or one under the root base.
    #[serde(default)]
    pub root_hint: Option<String>,
    /// The last component of the default root, `~/lemma/c/<date>/<slug>`.
    #[serde(default)]
    pub slug: Option<String>,
    /// `yyyy-mm-dd`, the conversation's day. Today when absent; the backend
    /// sends it so that a reopen tomorrow finds the same folder.
    #[serde(default)]
    pub date: Option<String>,
    /// Further folders the owner granted, writable beside the root.
    #[serde(default)]
    pub grants: Vec<String>,
    /// The root of the `lemma` CLI Lemma ships for this workspace's commands:
    /// a folder whose `bin/lemma` is its release of the CLI. Admitted only
    /// through `seatbelt::lemma_cli_root`.
    #[serde(default)]
    pub lemma_cli: Option<String>,
}

/// The default root: `<root base>/c/<yyyy-mm-dd>/<slug>`, the same folder an
/// Agent Host run for the conversation uses.
pub fn default_root(
    root_base: &Path,
    params: &OpenParams,
    workspace: &str,
) -> Result<PathBuf, OpFailure> {
    let date = match params.date.as_deref() {
        Some(date) => chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d")
            .map_err(|_| OpFailure::invalid(format!("date {date:?} is not yyyy-mm-dd")))?,
        None => chrono::Local::now().date_naive(),
    };
    let slug = match params.slug.as_deref() {
        Some(slug) => slug.to_owned(),
        None => params
            .conversation_id
            .map_or_else(|| workspace.to_owned(), |id| id.to_string()),
    };
    if !safe_component(&slug) {
        return Err(OpFailure::invalid(format!(
            "{slug:?} cannot name a folder: use letters, digits, '.', '_' and '-'"
        )));
    }
    Ok(root_base
        .join("c")
        .join(date.format("%Y-%m-%d").to_string())
        .join(slug))
}

fn safe_component(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && !value.starts_with('.')
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "._-".contains(character))
}

/// Whether a folder Lemma names may be given to a workspace: it is a real
/// folder the owner bound a conversation to on this machine, or it is under
/// the root base. Never the home folder or anything that contains it, whose
/// write access would reach every dotfile an allow-list is meant to protect.
///
/// The backend naming a path is not the owner choosing it. Bound folders are
/// recorded by the desktop shell from a native folder dialog (see
/// `conversation_folders`); this is the same rule, applied to host execution.
#[must_use]
pub fn admissible(
    folder: &Path,
    root_base: &Path,
    home: &Path,
    bound: &[PathBuf],
) -> Option<PathBuf> {
    if !folder.is_absolute() {
        return None;
    }
    let canonical = std::fs::canonicalize(folder).ok()?;
    if !canonical.is_dir() {
        return None;
    }
    let home = std::fs::canonicalize(home).unwrap_or_else(|_| home.to_path_buf());
    if home.starts_with(&canonical) {
        return None;
    }
    // Nothing hidden: `~/lemma/.lemma` holds the directory registry, and no
    // conversation's folder is spelt with a dot.
    let under_base = std::fs::canonicalize(root_base).is_ok_and(|base| {
        canonical.strip_prefix(&base).is_ok_and(|relative| {
            relative.components().next().is_some()
                && relative.components().all(|component| {
                    matches!(component, Component::Normal(name)
                        if !name.to_string_lossy().starts_with('.'))
                })
        })
    });
    let chosen = bound
        .iter()
        .any(|bound| std::fs::canonicalize(bound).is_ok_and(|bound| bound == canonical));
    (under_base || chosen).then_some(canonical)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    struct Fixture {
        _directory: tempfile::TempDir,
        root: PathBuf,
        outside: PathBuf,
        policy: PathPolicy,
    }

    fn fixture() -> Fixture {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().join("root");
        let tmp = directory.path().join("tmp");
        let outside = directory.path().join("outside");
        for folder in [&root, &tmp, &outside] {
            std::fs::create_dir(folder).unwrap();
        }
        let policy = PathPolicy::new(&root, &tmp, &[]).unwrap();
        Fixture {
            root: std::fs::canonicalize(&root).unwrap(),
            outside: std::fs::canonicalize(&outside).unwrap(),
            _directory: directory,
            policy,
        }
    }

    #[test]
    fn a_path_inside_the_root_resolves_whether_or_not_it_exists() {
        let fixture = fixture();
        let absolute = fixture.root.join("a/b/new.txt");
        assert_eq!(
            fixture
                .policy
                .resolve(absolute.to_str().unwrap(), Leaf::Follow)
                .unwrap(),
            absolute
        );
        assert_eq!(
            fixture.policy.resolve("rel.txt", Leaf::Follow).unwrap(),
            fixture.root.join("rel.txt")
        );
    }

    #[test]
    fn dot_dot_cannot_climb_out_of_the_root() {
        let fixture = fixture();
        let error = fixture
            .policy
            .resolve("../outside/x", Leaf::Follow)
            .unwrap_err();
        assert_eq!(error.kind, kind::OUTSIDE_WORKSPACE);
        let sneaky = format!("{}/../outside", fixture.root.display());
        let error = fixture.policy.resolve(&sneaky, Leaf::Follow).unwrap_err();
        assert_eq!(error.kind, kind::OUTSIDE_WORKSPACE);
        // `..` below a folder that does not exist is not guessed at.
        let error = fixture
            .policy
            .resolve("missing/../../outside", Leaf::Follow)
            .unwrap_err();
        assert_ne!(error.kind, "ok");
    }

    #[test]
    fn a_symlink_out_of_the_root_is_refused_even_for_a_file_that_does_not_exist() {
        let fixture = fixture();
        std::os::unix::fs::symlink(&fixture.outside, fixture.root.join("escape")).unwrap();
        for path in ["escape", "escape/new.txt", "escape/deeper/new.txt"] {
            let error = fixture.policy.resolve(path, Leaf::Follow).unwrap_err();
            assert_eq!(error.kind, kind::OUTSIDE_WORKSPACE, "{path}");
        }
        // The link itself is inside, and may be deleted.
        assert_eq!(
            fixture.policy.resolve("escape", Leaf::NoFollow).unwrap(),
            fixture.root.join("escape")
        );
    }

    #[test]
    fn the_temporary_directory_and_grants_are_inside() {
        let fixture = fixture();
        let grant = fixture.outside.clone();
        let policy = PathPolicy::new(
            &fixture.root,
            &fixture.root.parent().unwrap().join("tmp"),
            std::slice::from_ref(&grant),
        )
        .unwrap();
        assert!(
            policy
                .resolve(grant.join("x").to_str().unwrap(), Leaf::Follow)
                .is_ok()
        );
        let tmp = fixture.root.parent().unwrap().join("tmp/y");
        assert!(policy.resolve(tmp.to_str().unwrap(), Leaf::Follow).is_ok());
        assert!(policy.resolve("/etc/hosts", Leaf::Follow).is_err());
    }

    #[test]
    fn the_default_root_is_the_conversation_folder_and_refuses_unsafe_slugs() {
        let params = OpenParams {
            slug: Some("fix-the-build".into()),
            date: Some("2026-09-25".into()),
            ..OpenParams::default()
        };
        assert_eq!(
            default_root(Path::new("/base"), &params, "w").unwrap(),
            PathBuf::from("/base/c/2026-09-25/fix-the-build")
        );
        for slug in ["../x", "a/b", ".hidden", ""] {
            let params = OpenParams {
                slug: Some(slug.into()),
                ..OpenParams::default()
            };
            assert!(
                default_root(Path::new("/base"), &params, "w").is_err(),
                "{slug}"
            );
        }
        let params = OpenParams {
            date: Some("25/09/2026".into()),
            ..OpenParams::default()
        };
        assert!(default_root(Path::new("/base"), &params, "w").is_err());
    }

    #[test]
    fn only_bound_folders_and_folders_under_the_base_are_admissible() {
        let directory = tempfile::tempdir().unwrap();
        let home = directory.path().join("home");
        let base = home.join("lemma");
        let project = home.join("project");
        let other = home.join("other");
        for folder in [
            &base,
            &project,
            &other,
            &base.join("c/x"),
            &base.join(".lemma"),
        ] {
            std::fs::create_dir_all(folder).unwrap();
        }
        let bound = [project.clone()];
        assert!(admissible(&project, &base, &home, &bound).is_some());
        assert!(admissible(&base.join("c/x"), &base, &home, &bound).is_some());
        assert!(admissible(&other, &base, &home, &bound).is_none());
        assert!(admissible(&base, &base, &home, &bound).is_none());
        // The directory registry is not a conversation's folder.
        assert!(admissible(&base.join(".lemma"), &base, &home, &bound).is_none());
        // Even when bound: the home folder would open every dotfile.
        assert!(admissible(&home, &base, &home, std::slice::from_ref(&home)).is_none());
        assert!(admissible(Path::new("/"), &base, &home, &[PathBuf::from("/")]).is_none());
    }
}
