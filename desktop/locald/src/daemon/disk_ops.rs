//! Giving disk back: the pre-migration backup, unused images, trimmed blocks.
//!
//! The rules are `crate::disk_hygiene`'s; this is when they run. After every
//! clean start, hourly while the daemon lives (which is what makes "weekly"
//! and "three days at the latest" hold for an app left open), and when the
//! person asks from This Mac.

use super::*;
use crate::disk_hygiene::{self, BackupVerdict, HygieneRecord};
use std::time::{Duration, SystemTime};

/// One hygiene monitor per daemon process.
static MONITOR_STARTED: AtomicBool = AtomicBool::new(false);

const MONITOR_INTERVAL: Duration = Duration::from_secs(60 * 60);

impl Daemon {
    /// The numbers This Mac shows, for `control.snapshot`.
    pub(super) fn disk_usage_snapshot(&self) -> Value {
        disk_hygiene::disk_usage(&self.paths.root, SystemTime::now())
    }

    /// The stack just reported ready: the backend is healthy, after its
    /// migrations if it had any.
    pub(super) fn after_clean_start(self: &Arc<Self>) {
        let daemon = Arc::clone(self);
        thread::spawn(move || daemon.tidy_disk(true));
    }

    /// Hourly, for the life of the daemon; the first pass runs at launch, so a
    /// backup past its three days goes even when no start ever succeeds.
    pub(super) fn start_disk_hygiene_monitor(self: &Arc<Self>) {
        if MONITOR_STARTED.swap(true, Ordering::SeqCst) {
            return;
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || loop {
            daemon.tidy_disk(false);
            let mut waited = Duration::ZERO;
            while waited < MONITOR_INTERVAL {
                if daemon.lifecycle.checkpoint().is_err() {
                    return;
                }
                thread::sleep(Duration::from_secs(30));
                waited += Duration::from_secs(30);
            }
        });
    }

    fn tidy_disk(&self, clean_start: bool) {
        self.sweep_update_backup(clean_start);
        let Some(release) = self.running_release() else {
            return;
        };
        let record = disk_hygiene::read_record(&self.paths.root);
        let now = disk_hygiene::unix_seconds(SystemTime::now());
        if disk_hygiene::image_prune_due(record.as_ref(), &release, now) {
            match self.prune_images_and_trim(&release) {
                Ok(summary) => {
                    let _ = self.write_daemon_log(&format!("disk hygiene: {summary}"));
                }
                Err(error) => {
                    let _ = self.write_daemon_log(&format!("disk hygiene: images kept: {error}"));
                }
            }
        }
    }

    /// The release that is serving right now, or `None` when nothing is.
    fn running_release(&self) -> Option<String> {
        let ready = self.state.lock().expect("state lock poisoned").ready;
        if !ready || self.managed_runtime.is_none() || self.lifecycle.busy() {
            return None;
        }
        self.host_processes
            .as_ref()
            .map(|manager| manager.release().to_owned())
    }

    /// Apply the backup rule, only while no start, stop or reset is running:
    /// a start that migrates replaces this very file.
    ///
    /// Waits for the lifecycle to be idle rather than taking it. Taking it
    /// would refuse a Start or Quit the person pressed in that instant as
    /// "busy", for a background tidy-up they never asked for; and a start
    /// reaches its migration snapshot only after seconds of other work, far
    /// longer than the stat and unlink below.
    fn sweep_update_backup(&self, clean_start: bool) {
        let root = &self.paths.root;
        let path = root.join(disk_hygiene::UPDATE_BACKUP);
        if !path.exists() {
            return;
        }
        // A clean start is reported a moment before the start that reported
        // it releases the lifecycle; wait briefly rather than skip.
        for _ in 0..60 {
            if !self.lifecycle.busy() || self.lifecycle.checkpoint().is_err() {
                break;
            }
            thread::sleep(Duration::from_secs(1));
        }
        if self.lifecycle.busy() {
            return;
        }
        let migration_failed = UpdateTransaction::load(root.join("update.json"))
            .ok()
            .and_then(|transaction| transaction.blocking_reason())
            .is_some();
        let migrated_release = crate::host_process::read_schema_release(root);
        let started_cleanly_on_migrated_release = clean_start
            && migrated_release.is_some()
            && migrated_release.as_deref()
                == self
                    .host_processes
                    .as_ref()
                    .map(|manager| manager.release());
        let age = disk_hygiene::backup_taken_at(&path)
            .and_then(|taken| SystemTime::now().duration_since(taken).ok());
        let verdict = disk_hygiene::backup_verdict(
            age,
            migration_failed,
            started_cleanly_on_migrated_release,
        );
        let reason = match verdict {
            BackupVerdict::Keep => return,
            BackupVerdict::RemoveAfterCleanStart => "the updated release started cleanly",
            BackupVerdict::RemoveExpired => "it was three days old",
        };
        let line = match disk_hygiene::remove_backup(root) {
            Ok(freed) => format!(
                "removed the pre-migration backup because {reason}{}",
                freed
                    .map(|bytes| format!(" ({} freed)", disk_hygiene::format_bytes(bytes)))
                    .unwrap_or_default()
            ),
            Err(error) => format!("could not remove the pre-migration backup: {error}"),
        };
        let _ = self.write_daemon_log(&line);
    }

    /// Prune unused images, trim, and record that it happened.
    fn prune_images_and_trim(&self, release: &str) -> io::Result<String> {
        let runtime = self
            .managed_runtime
            .as_ref()
            .ok_or_else(|| io::Error::other("no managed runtime"))?;
        let pruned = runtime.prune_unused_images()?;
        let removed = pruned["removed"].as_array().map_or(0, Vec::len);
        // Recorded before the trim: a device that takes no discards should not
        // make the prune run again every hour.
        let _ = disk_hygiene::write_record(
            &self.paths.root,
            &HygieneRecord {
                last_image_prune_unix: disk_hygiene::unix_seconds(SystemTime::now()),
                last_image_prune_release: release.to_owned(),
            },
        );
        let trim = runtime
            .trim_data_disk()
            .unwrap_or_else(|error| json!({ "detail": error.to_string() }));
        Ok(format!("removed {removed} unused image(s); trim {trim}"))
    }

    /// `disk.cleanup`: what This Mac's "Free up space" and backup "Delete" do.
    ///
    /// The shell has already asked the person, natively, before sending
    /// `delete_backup`. Images and the trim need no question: nothing either
    /// removes can be in use or lost.
    pub(super) fn start_disk_cleanup(
        self: &Arc<Self>,
        request: Value,
        client: mpsc::SyncSender<String>,
    ) {
        let id = request.get("id").cloned();
        let delete_backup = request.get("delete_backup").and_then(Value::as_bool) == Some(true);
        let prune_images = request.get("prune_images").and_then(Value::as_bool) == Some(true);
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut answer = json!({
                "v": PROTOCOL_VERSION,
                "event": "disk.cleanup",
                "id": id,
            });
            if delete_backup {
                let Some(_turn) = daemon.lifecycle.enter() else {
                    daemon.send_direct(
                        &client,
                        error_event(
                            "busy",
                            "Lemma is starting or stopping; try again when it is ready",
                            id.as_ref(),
                        ),
                    );
                    return;
                };
                match disk_hygiene::remove_backup(&daemon.paths.root) {
                    Ok(freed) => {
                        answer["backup_freed_bytes"] = json!(freed);
                        let _ = daemon.write_daemon_log(
                            "removed the pre-migration backup because it was asked to",
                        );
                    }
                    Err(error) => {
                        daemon.send_direct(
                            &client,
                            error_event(
                                "backup-delete-failed",
                                format!("could not delete the backup: {error}"),
                                id.as_ref(),
                            ),
                        );
                        return;
                    }
                }
            }
            if prune_images {
                match daemon.running_release() {
                    Some(release) => match daemon.prune_images_and_trim(&release) {
                        Ok(summary) => answer["images"] = json!(summary),
                        Err(error) => answer["images_error"] = json!(error.to_string()),
                    },
                    None => {
                        answer["images_error"] =
                            json!("Lemma is not running, so its images were left for next time")
                    }
                }
            }
            answer["disk_usage"] = daemon.disk_usage_snapshot();
            daemon.send_direct(&client, answer);
        });
    }
}

#[cfg(test)]
#[path = "disk_ops_tests.rs"]
mod tests;
