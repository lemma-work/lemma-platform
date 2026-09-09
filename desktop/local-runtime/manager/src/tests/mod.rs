//! The runtime manager's guards, grouped the way the code they cover is
//! grouped.

use super::*;
use tempfile::tempdir;

mod capability;
mod data_disk;
mod diagnostics;
mod health;
mod recovery;
mod release;
mod repair;
mod windows_data;
mod wsl_command;
mod wsl_output;

/// Every source file of the runtime manager, concatenated.
///
/// `lib.rs` was 2,850 lines and the guards below scanned it by name. It is a
/// directory of modules now, and a guard still reading one file would go on
/// passing while covering a fraction of what it used to.
///
/// From disk rather than a list of `include_str!`s: a list somebody maintains
/// is how a module goes unscanned.
pub(super) fn manager_source() -> String {
    let directory = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(&directory)
        .expect("the manager's source directory")
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|kind| kind == "rs"))
        // The guards themselves, which would otherwise let a scan find its
        // own needles.
        .filter(|path| path.file_name().is_some_and(|name| name != "tests.rs"))
        .collect();
    files.sort();
    assert!(
        files.len() > 8,
        "the manager is a directory of modules; reading {} file(s) means the \
         scan is looking at a fraction of it",
        files.len(),
    );
    files
        .iter()
        .map(|path| std::fs::read_to_string(path).expect("a manager module"))
        .collect::<Vec<_>>()
        .join("\n")
        .replace("\r\n", "\n")
}

/// `lib.rs` was the runtime manager; it is a module list now.
///
/// Three guards went on reading it by name after the split, and a guard that
/// searches less of the tree than it used to does not fail -- it passes.
#[test]
pub(super) fn no_guard_looks_for_the_manager_inside_lib() {
    // Assembled at compile time so this guard does not find itself.
    let needle = concat!("include_str!(\"lib", ".rs\")");
    let guards = std::fs::read_dir(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src")
            .join("tests"),
    )
    .expect("the guard directory")
    .filter_map(Result::ok)
    .map(|entry| std::fs::read_to_string(entry.path()).expect("a guard module"))
    .collect::<Vec<_>>()
    .join("\n");
    assert!(
        !guards.contains(needle),
        "a guard that scans the manager's source reads manager_source(), \
         which sees every module rather than whichever one lib.rs still holds",
    );
}

#[cfg(target_os = "macos")]
pub(super) struct RecoveryTestChild(std::process::Child);

#[cfg(target_os = "macos")]
impl Drop for RecoveryTestChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
