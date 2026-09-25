//! What surrounds `alembic upgrade head`: a record that it is running, a copy
//! of the data disk to go back to, and a plain answer when the database is
//! newer than the code.
//!
//! Migrations are forward-only and the one step of a start that changes what
//! an earlier version can read. Before this nothing recorded that they were
//! running, nothing could be restored if one went wrong, and a database a
//! newer Lemma had migrated -- a nightly followed by a stable, or a downgrade --
//! failed five times over with Alembic's "Can't locate revision" and a generic
//! setup error.

use super::*;
use crate::update_transaction::{UpdatePhase, UpdateTransaction};

pub(crate) const MIGRATIONS_SETUP_ID: &str = "migrations";

/// The release whose migrations this database last completed, beside the stamps.
const SCHEMA_RELEASE_FILE: &str = "schema-release";

/// The copy of the data disk taken just before migrations change it.
#[cfg(target_os = "macos")]
pub(crate) const PRE_MIGRATION_DISK: &str = "runtime/macos/data.raw.before-migration";

/// The data disk's identity, so a stamp cannot outlive the database it describes.
///
/// Setup stamps live on the Mac and the database on the data disk. A disk
/// that was replaced -- discarded by a reset, recreated after a repair, deleted
/// by hand -- came back empty while `setup-stamps.json` still said its
/// migrations had run, and the backend started against no tables at all.
/// Inode and birth time together name one file: a recreated disk gets new ones.
#[cfg(target_os = "macos")]
pub(crate) fn data_disk_identity(root: &Path) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    let metadata = fs::metadata(root.join("runtime/macos/data.raw")).ok()?;
    let born = metadata
        .created()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_nanos();
    Some(format!("disk-{}-{born}", metadata.ino()))
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn data_disk_identity(_root: &Path) -> Option<String> {
    None
}

/// A setup's declared stamp, bound to the data disk it was recorded against.
pub(crate) fn bound_stamp(stamp: &str, disk: Option<&str>) -> String {
    match disk {
        Some(disk) => format!("{stamp}@{disk}"),
        None => stamp.to_owned(),
    }
}

/// The revision Alembic could not find, when that is why it failed.
///
/// That error means the database was migrated by code newer than this: its
/// `alembic_version` names a revision this build has never heard of.
pub(crate) fn unknown_revision(log: &str) -> Option<String> {
    const MARKER: &str = "Can't locate revision identified by ";
    let line = log.lines().rev().find(|line| line.contains(MARKER))?;
    let rest = &line[line.find(MARKER)? + MARKER.len()..];
    Some(
        rest.trim()
            .trim_matches(|c| c == '\'' || c == '"')
            .to_owned(),
    )
}

pub(crate) fn newer_database_message(
    revision: &str,
    this_release: &str,
    migrated_by: Option<&str>,
) -> String {
    let newer = match migrated_by {
        Some(release) => format!("Lemma {release}"),
        None => "a newer version of Lemma".to_owned(),
    };
    format!(
        "This installation's data was last updated by {newer} (database revision \
         {revision}), and Lemma {this_release} cannot read it. Install {newer} or \
         later to open it. Nothing has been changed."
    )
}

pub(crate) struct MigrationRun {
    root: PathBuf,
    transaction: Option<UpdateTransaction>,
}

impl MigrationRun {
    /// Record that migrations are about to run, and keep a copy of the disk
    /// when there is a database worth keeping.
    ///
    /// `previously_migrated` is whether a migrations stamp was ever recorded:
    /// a first start has nothing to protect and pays nothing.
    pub(crate) fn begin(root: &Path, release: &str, previously_migrated: bool) -> Self {
        let from = read_schema_release(root).unwrap_or_else(|| "unknown".to_owned());
        let transaction = UpdateTransaction::load(root.join("update.json"))
            .and_then(|transaction| {
                // An earlier start stopped while migrating. Running them again
                // is the only way forward -- the schema has already moved --
                // and it is exactly what this start is about to do.
                if transaction.blocking_reason().is_some() {
                    transaction.resolve()?;
                }
                transaction.begin(&from, release)?;
                transaction.advance(UpdatePhase::Migrating)?;
                Ok(transaction)
            })
            .map_err(|error| {
                eprintln!("locald: could not record the migration: {error}");
            })
            .ok();
        if previously_migrated {
            if let Err(error) = snapshot_data_disk(root) {
                eprintln!("locald: no pre-migration copy of the data disk: {error}");
            }
        }
        Self {
            root: root.to_path_buf(),
            transaction,
        }
    }

    /// Alembic refused before changing anything; there is nothing to record.
    pub(crate) fn abandon_unchanged(self) {
        if let Some(transaction) = self.transaction {
            let _ = transaction.commit();
        }
    }

    pub(crate) fn succeeded(self, release: &str) {
        let _ = write_private_atomic(
            &self.root.join(SCHEMA_RELEASE_FILE),
            format!("{release}\n").as_bytes(),
        );
        if let Some(transaction) = self.transaction {
            let _ = transaction.commit();
        }
    }
}

/// A local-data reset erases the database, so what it recorded goes with it --
/// including the pre-migration copy, which is a copy of the data being erased.
pub(crate) fn forget_migration_records(root: &Path) -> io::Result<()> {
    let mut records = vec![root.join(SCHEMA_RELEASE_FILE)];
    #[cfg(target_os = "macos")]
    records.push(root.join(PRE_MIGRATION_DISK));
    for record in records {
        match fs::remove_file(record) {
            Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error),
            _ => {}
        }
    }
    Ok(())
}

pub(crate) fn read_schema_release(root: &Path) -> Option<String> {
    let text = fs::read_to_string(root.join(SCHEMA_RELEASE_FILE)).ok()?;
    let release = text.trim();
    (!release.is_empty() && release.len() <= 64).then(|| release.to_owned())
}

/// An APFS clone of the data disk: instant, and costing space only as the
/// live disk diverges from it. Replaced by each migration, so there is only
/// ever one. Crash-consistent -- taken with Postgres running -- which is what
/// Postgres is built to recover from.
#[cfg(target_os = "macos")]
fn snapshot_data_disk(root: &Path) -> io::Result<()> {
    use std::os::unix::ffi::OsStrExt;
    let disk = root.join("runtime/macos/data.raw");
    if !disk.exists() {
        return Ok(());
    }
    let copy = root.join(PRE_MIGRATION_DISK);
    match fs::remove_file(&copy) {
        Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error),
        _ => {}
    }
    let source = std::ffi::CString::new(disk.as_os_str().as_bytes())
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
    let target = std::ffi::CString::new(copy.as_os_str().as_bytes())
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
    // SAFETY: both paths are NUL-terminated and live for the call.
    if unsafe { libc::clonefile(source.as_ptr(), target.as_ptr(), 0) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn snapshot_data_disk(_root: &Path) -> io::Result<()> {
    Ok(())
}
