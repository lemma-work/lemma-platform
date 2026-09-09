//! Windows path prefixes, which the children cannot read.

use super::*;

/// Every path that reaches the manifest goes through the stripper.
///
/// The first attempt at this fixed the two roots and missed the leaves:
/// `required_file` ended in `candidate.canonicalize()`, which put the
/// prefix straight back on node.exe, the launcher and server.js. The
/// build looked fixed and the frontend failed exactly as before.
///
/// A source assertion rather than a behavioural one, because the property
/// is "nobody adds another bare canonicalize", and the only way to observe
/// it otherwise is on Windows with a real pack.
#[test]
fn nothing_here_canonicalizes_a_path_without_stripping_the_prefix() {
    // The guards are left out: this test names the string it looks for, and
    // would otherwise find itself.
    let source = pack_source();
    let bare: Vec<&str> = source
        .lines()
        .map(str::trim)
        .filter(|line| line.contains(".canonicalize()"))
        .filter(|line| !line.starts_with("//"))
        .collect();
    assert_eq!(
        bare,
        ["let canonical = path.canonicalize()?;"],
        "paths from here are handed to other programs, so they go through \
         `canonicalize_for_children`; the only bare call is the one inside it"
    );
}

/// Node reads `\\?\C:\...` as a UNC path and dies on `lstat 'C:'`.
///
/// `canonicalize` returns that prefix on Windows, every path in the
/// generated manifest descends from a canonicalized root, and those paths
/// are handed to other programs. Rust and `CreateProcess` both accept it,
/// which is why it went unnoticed until a Windows machine tried to start
/// the frontend and Node reported
/// `EISDIR: illegal operation on a directory, lstat 'C:'` -- server `?`,
/// share `C:`.
///
/// String-level, so it is tested on every platform rather than only on the
/// one where it matters.
#[test]
fn a_drive_path_loses_the_prefix_and_a_real_unc_path_keeps_it() {
    assert_eq!(
        super::without_verbatim_prefix(r"\\?\C:\Users\a\frontend-launcher.mjs"),
        Some(r"C:\Users\a\frontend-launcher.mjs"),
    );
    // A genuine UNC path: the prefix is not decoration there.
    assert_eq!(
        super::without_verbatim_prefix(r"\\?\UNC\server\share\file"),
        None,
    );
    // Already plain, or not a prefix at all.
    assert_eq!(super::without_verbatim_prefix(r"C:\Users\a"), None);
    assert_eq!(super::without_verbatim_prefix("/usr/local/bin/node"), None);
    // A drive letter with nothing after it is not a path to hand anyone.
    assert_eq!(super::without_verbatim_prefix(r"\\?\C:"), None);
}
