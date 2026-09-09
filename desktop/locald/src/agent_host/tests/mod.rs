//! The Agent Host supervisor's guards, grouped the way the code they cover
//! is grouped.

mod logs;
mod pairing;
mod process_groups;
mod reclaim;
mod restart;
mod status;

use super::*;
use tempfile::tempdir;

pub(super) fn write(path: &Path, contents: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, contents).unwrap();
}
