//! Kernel faults, and explaining an exit from the log.

use super::*;

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
