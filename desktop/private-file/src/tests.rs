//! The guards for the one guarantee six copies used to give differently.

use super::*;
use std::sync::Arc;

fn scratch() -> tempfile::TempDir {
    tempfile::tempdir().expect("a temporary directory")
}

#[test]
fn a_written_file_has_exactly_the_contents_it_was_given() {
    let root = scratch();
    let path = root.path().join("nested/deeper/state.json");
    write_atomic(&path, b"{\"a\":1}").expect("the parent directories are created");
    assert_eq!(std::fs::read(&path).unwrap(), b"{\"a\":1}");
}

#[test]
fn a_rewrite_replaces_rather_than_appends() {
    let root = scratch();
    let path = root.path().join("state.json");
    write_atomic(&path, b"first").unwrap();
    write_atomic(&path, b"second").unwrap();
    assert_eq!(std::fs::read(&path).unwrap(), b"second");
}

/// The temporary must never be left behind, whatever happens to it.
#[test]
fn nothing_is_left_beside_the_file_it_wrote() {
    let root = scratch();
    let path = root.path().join("state.json");
    write_atomic(&path, b"contents").unwrap();
    let left: Vec<String> = std::fs::read_dir(root.path())
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| name != "state.json")
        .collect();
    assert!(left.is_empty(), "left behind: {left:?}");
}

/// Two writers at once, which is what a daemon persisting state from two
/// threads actually does.
///
/// The implementations this replaced named the temporary from the process id
/// alone. Two writes in flight in one process therefore chose the same name,
/// and `create_new` failed the second with `AlreadyExists` -- a persist that
/// reported failure for a reason that reads like a permissions problem.
#[test]
fn two_writes_at_once_in_one_process_both_succeed() {
    let root = scratch();
    let path = Arc::new(root.path().join("state.json"));
    let barrier = Arc::new(std::sync::Barrier::new(8));
    let mut writers = Vec::new();
    for index in 0..8 {
        let path = Arc::clone(&path);
        let barrier = Arc::clone(&barrier);
        writers.push(std::thread::spawn(move || {
            barrier.wait();
            write_atomic(&path, format!("writer {index}").as_bytes())
        }));
    }
    for writer in writers {
        writer
            .join()
            .expect("the writer did not panic")
            .expect("every write succeeds");
    }
    // Whichever won, the file is one writer's contents entire.
    let contents = std::fs::read_to_string(path.as_path()).unwrap();
    assert!(
        (0..8).any(|index| contents == format!("writer {index}")),
        "a torn file: {contents:?}"
    );
}

#[cfg(unix)]
#[test]
fn a_written_file_is_private_and_a_widened_one_is_refused() {
    use std::os::unix::fs::PermissionsExt;
    let root = scratch();
    let path = root.path().join("secret.json");
    write_atomic(&path, b"secret").unwrap();
    let mode = std::fs::metadata(&path).unwrap().permissions().mode();
    assert_eq!(mode & 0o077, 0, "mode was {mode:o}");

    // And a file somebody else widened is reported rather than corrected.
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
    let error = ensure_private(&path).expect_err("a group-readable secret is refused");
    assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
}

#[cfg(unix)]
#[test]
fn a_symbolic_link_is_not_a_private_file() {
    let root = scratch();
    let real = root.path().join("real");
    std::fs::write(&real, b"x").unwrap();
    let link = root.path().join("link");
    std::os::unix::fs::symlink(&real, &link).unwrap();
    assert!(
        ensure_private(&link).is_err(),
        "a link is not a regular file"
    );
}

#[test]
fn a_path_with_no_parent_is_refused_rather_than_panicking() {
    let error = write_atomic(Path::new("/"), b"x").expect_err("the root has no parent");
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
}

#[cfg(unix)]
#[test]
fn a_private_directory_is_entered_only_by_this_user() {
    use std::os::unix::fs::PermissionsExt;
    let root = scratch();
    let path = root.path().join("vault");
    ensure_private_directory(&path).unwrap();
    let mode = std::fs::metadata(&path).unwrap().permissions().mode();
    assert_eq!(mode & 0o077, 0, "mode was {mode:o}");
}

#[test]
fn an_appending_log_keeps_what_was_already_there() {
    let root = scratch();
    let path = root.path().join("logs/run.log");
    {
        let mut log = appending_log(&path).unwrap();
        log.write_all(b"first\n").unwrap();
    }
    {
        let mut log = appending_log(&path).unwrap();
        log.write_all(b"second\n").unwrap();
    }
    assert_eq!(std::fs::read_to_string(&path).unwrap(), "first\nsecond\n");
}

/// The repairing counterpart, and why it is not the refusing one.
#[cfg(unix)]
#[test]
fn a_widened_file_is_narrowed_rather_than_refused_where_that_is_the_contract() {
    use std::os::unix::fs::PermissionsExt;
    let root = scratch();
    let path = root.path().join("config.json");
    std::fs::write(&path, b"{}").unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
    // `cp -R` instead of `cp -Rp` lands exactly here, and refusing was
    // permanently fatal to an installation, silently.
    make_private(&path).expect("a widened config is repaired");
    let mode = std::fs::metadata(&path).unwrap().permissions().mode();
    assert_eq!(mode & 0o077, 0, "mode was {mode:o}");
}

#[cfg(unix)]
#[test]
fn repairing_still_refuses_what_is_not_a_regular_file() {
    let root = scratch();
    let real = root.path().join("real");
    std::fs::write(&real, b"x").unwrap();
    let link = root.path().join("link");
    std::os::unix::fs::symlink(&real, &link).unwrap();
    let error = make_private(&link).expect_err("a link is not a file to repair");
    assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
    assert!(make_private(root.path()).is_err(), "nor is a directory");
}

/// A log that already existed is narrowed, not only one this call creates.
///
/// `OpenOptions::mode` applies to a file this call brings into existence and to
/// nothing else, so a log written by an older build that set no mode -- or one
/// copied without `-p` -- stayed as wide as it was while every appended line
/// made it worth more.
#[cfg(unix)]
#[test]
fn an_existing_log_is_narrowed_rather_than_left_as_it_was_found() {
    use std::os::unix::fs::PermissionsExt;
    let root = scratch();
    let path = root.path().join("run.log");
    std::fs::write(&path, b"already here\n").unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();

    {
        let mut log = appending_log(&path).unwrap();
        log.write_all(b"and this\n").unwrap();
    }

    let mode = std::fs::metadata(&path).unwrap().permissions().mode();
    assert_eq!(mode & 0o077, 0, "mode was {mode:o}");
    assert_eq!(
        std::fs::read_to_string(&path).unwrap(),
        "already here\nand this\n",
        "narrowing a log must not cost what was in it"
    );
}

/// Which directory-sync failures are forgiven, and which are not.
///
/// A filesystem that implements no `fsyncdir` -- a FUSE server, some network
/// mounts -- answers `EINVAL` or `ENOTSUP`. By then the rename has already
/// succeeded, so reporting it failed a write that had landed and skipped the
/// privacy check after it. Everything else is a real failure: the interesting
/// half of this guard is the second list, because forgiving too much turns a
/// device error into a write that claims to have been made durable.
#[test]
fn only_a_filesystem_that_will_not_sync_a_directory_is_forgiven() {
    for kind in [io::ErrorKind::InvalidInput, io::ErrorKind::Unsupported] {
        assert!(
            directory_sync_is_unsupported(&io::Error::new(kind, "no fsyncdir here")),
            "{kind:?} means the filesystem declined, not that the write failed"
        );
    }
    for kind in [
        io::ErrorKind::PermissionDenied,
        io::ErrorKind::NotFound,
        io::ErrorKind::Other,
    ] {
        assert!(
            !directory_sync_is_unsupported(&io::Error::new(kind, "a real failure")),
            "{kind:?} is a durability failure and must be reported"
        );
    }
}
