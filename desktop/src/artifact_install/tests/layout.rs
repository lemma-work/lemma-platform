//! The release directory's name, and the space one needs.

use super::*;

/// The release directory's name is what makes Windows work or not.
///
/// The whole SHA-256 went in it, and 64 hex characters put the deepest
/// file in the host pack 316 characters from the drive root. `MAX_PATH` is
/// 260. Measured on a real Windows installation: 1,349 files past the
/// limit, written happily because Rust uses the extended-length prefix, and
/// then unopenable by everything that does not -- including the pack's own
/// Python, which could not import a module sitting right there.
///
/// Eight hex characters take the same installation's worst path to 236,
/// which leaves 23 characters for a user name longer than the one it was
/// measured on.
#[test]
fn the_release_directory_name_fits_inside_a_windows_path() {
    let identity = short_identity(b"{\"release\":\"0.7.2\"}");
    assert_eq!(identity.len(), 8, "{identity}");
    assert!(identity.chars().all(|c| c.is_ascii_hexdigit()));

    // Still tells one set of artifacts from another, which is its job.
    assert_ne!(short_identity(b"a"), short_identity(b"b"));
    assert_eq!(short_identity(b"a"), short_identity(b"a"));

    // The arithmetic, so it cannot drift back: this directory, plus the
    // longest path the host pack is allowed to contain, has to fit.
    let directory = "C:\\Users\\a-twenty-char-name\\AppData\\Local\\Lemma\\runtime\\releases\\"
        .len()
        + "0.7.2-".len()
        + identity.len();
    // `WINDOWS_PATH_BUDGET` in scripts/build_local_host_pack.py.
    assert!(directory + 1 + 170 <= 259, "{directory}");
}

#[test]
fn runtime_above_previous_limits_reserves_downloads_extraction_and_headroom() {
    let gib = 1024 * 1024 * 1024;
    assert_eq!(installation_space_required(gib, 3 * gib).unwrap(), 8 * gib);
}

#[test]
fn runtime_size_boundaries_are_inclusive() {
    let gib = 1024 * 1024 * 1024;
    assert_eq!(
        installation_space_required(6 * gib, 8 * gib).unwrap(),
        18 * gib
    );
    for (download, expanded, message) in [
        (6 * gib + 1, 8 * gib, "archives exceed the 6 GiB"),
        (6 * gib, 8 * gib + 1, "runtime exceeds the 8 GiB"),
        (u64::MAX, 1, "archives exceed the 6 GiB"),
        (1, u64::MAX, "runtime exceeds the 8 GiB"),
    ] {
        let error = installation_space_required(download, expanded).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains(message), "{error}");
    }
}
