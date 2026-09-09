//! Kernel faults, and explaining an exit from the log.

use super::*;
use crate::lifecycle::needs_repair_reason;

#[cfg(target_os = "macos")]
#[test]
fn kernel_fault_blocks_work_before_dispatch_but_not_recovery_or_next_boot() {
    let root = tempdir().unwrap();
    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("missing-bridge"),
        vz_executable: root.path().join("missing-vz"),
    })
    .unwrap();
    let console = runtime.config.local_root.join("runtime/macos/console.log");
    fs::create_dir_all(console.parent().unwrap()).unwrap();
    fs::write(&console, "Internal error: Oops: 0000000096000004\n").unwrap();
    for error in [
        runtime.health().unwrap_err(),
        runtime.request("container.start", json!({})).unwrap_err(),
        runtime.wait_ready().unwrap_err(),
    ] {
        assert!(
            error.to_string().contains("guest kernel crashed"),
            "{error}"
        );
    }
    for operation in ["system.shutdown", "diagnostics.logs"] {
        let error = runtime.request(operation, json!({})).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::NotFound);
    }
    rotate_log(&console, 0).unwrap();
    runtime.check_guest_kernel().unwrap();
    assert_eq!(
        runtime
            .request("container.start", json!({}))
            .unwrap_err()
            .kind(),
        io::ErrorKind::NotFound
    );
}

#[test]
fn an_exit_is_explained_by_the_last_complaint_not_the_first_boot_retry() {
    // Every healthy boot dials the guest before guestd is listening, so
    // these are always the first lines in the log. Quoting them made an
    // exit minutes later read as though a connection reset had caused it.
    let log = b"lemma-vz: guest connect failed: Connection reset by peer\n\
                lemma-vz: guest connect failed: Connection reset by peer\n\
                lemma-vz: disk image is corrupt\n" as &[u8];
    assert_eq!(
        last_diagnostic(log, "fallback"),
        "lemma-vz: disk image is corrupt"
    );
}

#[test]
fn a_log_of_only_boot_retries_explains_nothing_and_says_so() {
    let log = b"lemma-vz: guest connect failed: Connection reset by peer\n\
                lemma-vz: guest connect failed: Connection reset by peer\n"
        as &[u8];
    assert_eq!(
        last_diagnostic(log, "the runtime log holds no explanation"),
        "the runtime log holds no explanation"
    );
}

/// The guest names this failure the same way on both platforms.
///
/// `lemma-mount-data` and its siblings print `lemma-data: needs-repair:
/// <reason>`, and only macOS was listening for it -- on Windows a guest that
/// had already said exactly what was wrong spent the host's whole two-minute
/// budget and arrived as "did not become ready".
#[test]
fn the_reason_a_guest_gives_for_needing_repair_is_read_wherever_it_says_it() {
    let console = "\
lemma-runtime: wsl.exe --distribution LemmaRuntime --exec /usr/local/bin/lemma-runtime-init -> exit code: 1
  stdout: lemma-data: needs-repair: no filesystem signature on /dev/sdc
  stderr: lemma-runtime-init: giving up
";
    assert_eq!(
        needs_repair_reason(console).as_deref(),
        Some("no filesystem signature on /dev/sdc"),
    );
}

/// The newest reason wins.
///
/// A guest that failed, was repaired, and failed again for a different reason
/// should report what it is stuck on now rather than what it got past.
#[test]
fn the_last_reason_is_the_one_that_is_reported() {
    let console = "\
  stdout: lemma-data: needs-repair: the data disk never appeared in the guest
  stdout: lemma-data: mounted
  stdout: lemma-data: needs-repair: /var/lib/lemma is not on the data disk
";
    assert_eq!(
        needs_repair_reason(console).as_deref(),
        Some("/var/lib/lemma is not on the data disk"),
    );
}

/// And an ordinary boot says nothing.
#[test]
fn a_guest_that_came_up_reports_no_reason() {
    assert!(needs_repair_reason("lemma-data: mounted\nlemma-guestd: listening\n").is_none());
    assert!(needs_repair_reason("").is_none());
    // The marker with nothing after it is not a reason; reporting an empty
    // one would put "needs repair: " in front of a person with no cause.
    assert!(needs_repair_reason("lemma-data: needs-repair:   \n").is_none());
}
