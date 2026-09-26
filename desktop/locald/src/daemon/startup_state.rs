//! State this launch derives, and what happens when it cannot be kept.

use super::*;
use crate::update_transaction::{UpdateRecord, UpdateStatus};

/// Something this start found that the person at this computer has to act on.
///
/// Structured, rather than the operator-log sentence `healed` carries, because
/// it crosses to three screens -- the splash, Local settings and This Mac --
/// that each want to say it with a title and a next step. `code` is what they
/// switch on; `message` is already written for a person.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct StartupWarning {
    pub code: &'static str,
    pub message: String,
    /// The Lemma version the warning asks for, when it names one.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
}

impl StartupWarning {
    pub(super) fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            version: None,
        }
    }
}

/// What an interrupted update asks of the person, or nothing.
///
/// Only an update that stopped once the database had moved matters: before
/// that the installed version is still the one the data belongs to. After it,
/// reopening the older version is the one move that turns an interruption
/// into lost data, so the sentence says which version to install instead.
/// `running` is every name this build answers to (its version and its runtime
/// release), because "install X" is wrong when X is already what is running --
/// starting it is what finishes the update.
pub(super) fn interrupted_update_warning(
    record: &UpdateRecord,
    running: &[&str],
) -> Option<StartupWarning> {
    let UpdateStatus::Interrupted { phase } = record.status else {
        return None;
    };
    if !phase.mutated_durable_state() {
        return None;
    }
    let known = |version: &str| !version.is_empty() && version != "unknown";
    let to = record.to_version.as_str();
    let older = if known(&record.from_version) {
        format!("Lemma {}", record.from_version)
    } else {
        "the older version".to_owned()
    };
    let (message, version) = if !known(to) {
        (
            "Your last update didn't finish. Install the newest Lemma to continue \u{2014} \
             don't reopen an older version, which may not be able to read your data."
                .to_owned(),
            None,
        )
    } else if running.contains(&to) {
        (
            format!(
                "Your last update to Lemma {to} didn't finish. Start Lemma to finish it \u{2014} \
                 don't reopen {older}, which may not be able to read your data."
            ),
            Some(to.to_owned()),
        )
    } else {
        (
            format!(
                "Your last update didn't finish. Install Lemma {to} to continue \u{2014} \
                 don't reopen the older version."
            ),
            Some(to.to_owned()),
        )
    };
    Some(StartupWarning {
        code: "update-interrupted",
        message,
        version,
    })
}

/// Every note `healed` holds, as a warning a screen can show.
///
/// `specific` pairs a note with the warning written for it; anything else was
/// repaired on the way up and is reported in the words the log uses, which
/// are already the operator's.
pub(super) fn startup_warnings(
    healed: &[String],
    specific: Vec<(String, StartupWarning)>,
) -> Vec<StartupWarning> {
    healed
        .iter()
        .map(|note| {
            specific
                .iter()
                .find(|(written_for, _)| written_for == note)
                .map(|(_, warning)| warning.clone())
                .unwrap_or_else(|| StartupWarning::new("startup-repaired", note.clone()))
        })
        .collect()
}

/// Write down the origin this launch derived, and carry on if it cannot be.
///
/// It used to be a `?`, which made an unwritable `state.json` -- a full disk, a
/// read-only volume, a permissions accident -- stop the daemon from starting at
/// all. Over a cache: the two URLs here are recomputed from the host pack's
/// reserved ports on every launch, which is what the comment above the
/// assignment says. The daemon that refused to start was refusing over
/// something it did not need.
///
/// Recorded rather than ignored. `healed` is where this daemon says what it had
/// to work around, and a state file it cannot write is worth an operator
/// hearing about even when nothing depended on it this time.
pub(super) fn remember_derived_origin(
    state: &StateSnapshot,
    path: &Path,
    healed: &mut Vec<String>,
) {
    if let Err(error) = state.persist(path) {
        healed.push(format!(
            "the local service state at {} could not be saved ({error}); Lemma is \
             running and will work this out again on the next start, but check \
             what is wrong with that file",
            path.display()
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::update_transaction::UpdatePhase;

    fn record(from: &str, to: &str, phase: UpdatePhase) -> UpdateRecord {
        UpdateRecord {
            schema_version: 1,
            from_version: from.into(),
            to_version: to.into(),
            started_at: 0,
            status: UpdateStatus::Interrupted { phase },
        }
    }

    #[test]
    fn an_interrupted_migration_asks_for_the_newer_version_by_name() {
        let warning = interrupted_update_warning(
            &record("0.7.2", "0.8.0", UpdatePhase::Migrating),
            &["0.7.2"],
        )
        .expect("the database moved, so this has to be said");
        assert_eq!(warning.code, "update-interrupted");
        assert_eq!(warning.version.as_deref(), Some("0.8.0"));
        assert!(
            warning.message.contains("Install Lemma 0.8.0"),
            "{}",
            warning.message
        );
        assert!(
            warning.message.contains("don't reopen"),
            "{}",
            warning.message
        );
    }

    #[test]
    fn an_interrupted_update_to_this_version_says_to_start_it_not_install_it() {
        let warning = interrupted_update_warning(
            &record("0.7.2", "0.8.0", UpdatePhase::Validating),
            &["0.8.0"],
        )
        .unwrap();
        assert!(
            warning.message.contains("Start Lemma to finish"),
            "{}",
            warning.message
        );
        assert!(
            warning.message.contains("Lemma 0.7.2"),
            "{}",
            warning.message
        );
        assert!(!warning.message.contains("Install"), "{}", warning.message);
    }

    #[test]
    fn an_unreadable_record_names_no_version_it_does_not_know() {
        let warning = interrupted_update_warning(
            &record("unknown", "unknown", UpdatePhase::Validating),
            &["0.8.0"],
        )
        .unwrap();
        assert_eq!(warning.version, None);
        assert!(!warning.message.contains("unknown"), "{}", warning.message);
    }

    #[test]
    fn an_update_that_stopped_before_migrating_asks_nothing() {
        assert!(interrupted_update_warning(
            &record("0.7.2", "0.8.0", UpdatePhase::Draining),
            &["0.7.2"]
        )
        .is_none());
    }

    #[test]
    fn every_healed_note_becomes_a_warning_and_specific_ones_keep_their_copy() {
        let healed = vec!["raw log sentence".to_owned(), "token replaced".to_owned()];
        let specific = vec![(
            "raw log sentence".to_owned(),
            StartupWarning::new("settings-writes-disabled", "friendly"),
        )];
        let warnings = startup_warnings(&healed, specific);
        assert_eq!(
            warnings,
            vec![
                StartupWarning::new("settings-writes-disabled", "friendly"),
                StartupWarning::new("startup-repaired", "token replaced"),
            ]
        );
    }

    /// A state file that cannot be written does not stop the daemon.
    ///
    /// It used to: `Daemon::new` persisted the origin it had just derived with a
    /// `?`, so a full disk, a read-only volume or a permissions accident refused
    /// the whole start. Over a cache — those two URLs are recomputed from the host
    /// pack's reserved ports on every launch, which is exactly what the comment
    /// above them says.
    ///
    /// Said out loud, though. Working around something silently is how the next
    /// person inherits a mystery instead of a message.
    #[test]
    fn an_unwritable_state_file_is_reported_rather_than_fatal() {
        let root = tempfile::tempdir().unwrap();
        // A directory where the file belongs: the write fails for a reason that
        // has nothing to do with what is being written.
        let path = root.path().join("state.json");
        std::fs::create_dir_all(&path).unwrap();

        let mut healed = Vec::new();
        remember_derived_origin(&Default::default(), &path, &mut healed);

        assert_eq!(healed.len(), 1, "{healed:?}");
        assert!(
            healed[0].contains("could not be saved"),
            "the operator has to be told: {}",
            healed[0]
        );
        assert!(
            healed[0].contains("will work this out again"),
            "and told it is not fatal: {}",
            healed[0]
        );
    }

    /// And the ordinary case still writes it.
    #[test]
    fn a_writable_state_file_is_saved_without_comment() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("state.json");

        let mut healed = Vec::new();
        remember_derived_origin(&Default::default(), &path, &mut healed);

        assert!(healed.is_empty(), "nothing went wrong: {healed:?}");
        assert!(path.is_file(), "the state was persisted");
    }
}
