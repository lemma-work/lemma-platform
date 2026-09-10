//! A repair verdict, the log it came from, and the epoch beside it.

use super::*;

/// A repair verdict from a previous boot cannot condemn this one.
///
/// `guest_needs_data_repair` scans the console for
/// `lemma-data: needs-repair:` and returns *before* health is ever polled,
/// and the console is append-only. So one bad boot condemned every boot
/// after it -- including the boot that follows a successful reset, which
/// found the old line, refused to start, and offered the same reset again.
/// The only escape was a full reinstall.
///
/// The fix is in `start`, which now rotates the console unconditionally
/// rather than at 5 MiB, so this method can only ever see the current boot.
#[cfg(target_os = "macos")]
#[test]
fn a_repair_verdict_does_not_outlive_the_boot_that_produced_it() {
    let root = tempdir().unwrap();
    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("lemma-runtime"),
        vz_executable: root.path().join("lemma-vz"),
    })
    .unwrap();
    let console = runtime.config.local_root.join("runtime/macos/console.log");
    fs::create_dir_all(console.parent().unwrap()).unwrap();
    fs::write(
        &console,
        "[    0.10] booting\nlemma-data: needs-repair: no filesystem signature on /dev/vdb\n",
    )
    .unwrap();

    assert_eq!(
        runtime.guest_needs_data_repair().as_deref(),
        Some("no filesystem signature on /dev/vdb"),
        "the verdict is read while it is this boot's",
    );

    // What `start` does on the next boot.
    rotate_log(&console, 0).unwrap();

    assert_eq!(
        runtime.guest_needs_data_repair(),
        None,
        "a verdict from a previous boot must not refuse this one",
    );
    // And it is kept, because the boot that failed is the one worth reading.
    assert!(fs::read_to_string(console.with_extension("previous.log"))
        .unwrap()
        .contains("needs-repair"),);
}

/// The rotation `start` relies on fires for any non-empty log.
#[test]
fn rotating_at_zero_moves_every_line_aside() {
    let root = tempdir().unwrap();
    let path = root.path().join("console.log");

    // A log that does not exist yet is not an error and leaves nothing.
    rotate_log(&path, 0).unwrap();
    assert!(!path.with_extension("previous.log").exists());

    fs::write(&path, "one line\n").unwrap();
    rotate_log(&path, 0).unwrap();
    assert_eq!(fs::read_to_string(&path).unwrap(), "");
    assert_eq!(
        fs::read_to_string(path.with_extension("previous.log")).unwrap(),
        "one line\n"
    );
}

#[cfg(target_os = "macos")]
#[test]
fn refreshes_private_host_epoch_for_direct_boot_guests() {
    let root = tempdir().unwrap();
    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("lemma-runtime"),
        vz_executable: root.path().join("lemma-vz"),
    })
    .unwrap();

    runtime.refresh_host_epoch().unwrap();

    let epoch: u64 = fs::read_to_string(&runtime.host_epoch_file)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert!(epoch > 1_700_000_000);
    ensure_private_file(&runtime.host_epoch_file).unwrap();
}
