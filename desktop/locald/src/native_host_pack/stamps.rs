//! What decides whether the one-time setups need to run again.

use super::*;

/// One stamp value from the things a setup's result depends on.
///
/// Hashed rather than concatenated so the manifest never carries a path or a
/// key's presence in readable form, and so the value stays a fixed width
/// whatever goes into it.
pub(crate) fn setup_stamp(parts: &[&str]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part.as_bytes());
        // Length-delimited: without this, ("ab", "c") and ("a", "bc") hash the
        // same, and two different states would share a stamp.
        hasher.update([0u8]);
    }
    hex::encode(hasher.finalize())
}

/// A fingerprint of the migration revisions a pack carries.
///
/// Names only, not contents: the file set changes when a revision is added or
/// removed, which is the case that matters, and reading every file on each
/// start would trade one cost for another. Empty when the directory cannot be
/// read, which makes the stamp depend on the release alone -- the conservative
/// direction, since a stamp that cannot be computed should not become a stamp
/// that matches.
pub(crate) fn migrations_fingerprint(backend_dir: &Path) -> String {
    let Ok(entries) = fs::read_dir(backend_dir.join("migrations/versions")) else {
        return String::new();
    };
    let mut names: Vec<String> = entries
        .flatten()
        .filter_map(|entry| entry.file_name().into_string().ok())
        .filter(|name| name.ends_with(".py"))
        .collect();
    names.sort();
    let mut hasher = Sha256::new();
    for name in &names {
        hasher.update(name.as_bytes());
        hasher.update([0u8]);
    }
    hex::encode(hasher.finalize())
}
