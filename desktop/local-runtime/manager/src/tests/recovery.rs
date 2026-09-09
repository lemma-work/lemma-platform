//! Reclaiming a VM helper this installation left behind.

use super::*;

#[cfg(target_os = "macos")]
#[test]
fn confirmed_recovery_reclaims_a_verified_helper_from_a_replaced_bundle() {
    let root = tempdir().unwrap();
    let mut runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("lemma-runtime"),
        vz_executable: PathBuf::from("/bin/sleep"),
    })
    .unwrap();
    let mut child = RecoveryTestChild(Command::new("/bin/sleep").arg("10").spawn().unwrap());
    runtime.record_macos_vm(&child.0).unwrap();
    runtime.config.vz_executable = root.path().join("new-app/lemma-vz");
    assert!(runtime.reclaim_owned_macos_vm().is_err());
    assert!(runtime.vm_process_marker.exists());
    thread::scope(|scope| {
        let reclaim = scope.spawn(|| runtime.reclaim_owned_macos_vm_for_reset());
        let status = child.0.wait().unwrap();
        reclaim.join().unwrap().unwrap();
        assert!(!status.success());
    });
    assert!(!runtime.vm_process_marker.exists());
}

#[cfg(target_os = "macos")]
#[test]
fn recovery_never_signals_a_reused_process_identity() {
    let root = tempdir().unwrap();
    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("lemma-runtime"),
        vz_executable: PathBuf::from("/bin/sleep"),
    })
    .unwrap();
    let mut child = RecoveryTestChild(Command::new("/bin/sleep").arg("10").spawn().unwrap());
    runtime.record_macos_vm(&child.0).unwrap();
    let mut marker: serde_json::Value =
        serde_json::from_slice(&fs::read(&runtime.vm_process_marker).unwrap()).unwrap();
    marker["start_identity"] = serde_json::json!("different process start");
    fs::write(
        &runtime.vm_process_marker,
        serde_json::to_vec(&marker).unwrap(),
    )
    .unwrap();
    runtime.reclaim_owned_macos_vm_for_reset().unwrap();
    assert!(child.0.try_wait().unwrap().is_none());
}

#[cfg(target_os = "macos")]
#[test]
fn reclaims_only_the_exact_recorded_vm_helper_across_daemon_replacement() {
    let root = tempdir().unwrap();
    let runtime = ManagedRuntime::new(ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("lemma-runtime"),
        vz_executable: PathBuf::from("/bin/sleep"),
    })
    .unwrap();
    let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
    runtime.record_macos_vm(&child).unwrap();
    let waiter = thread::spawn(move || child.wait().unwrap());

    runtime.reclaim_owned_macos_vm().unwrap();

    assert!(!waiter.join().unwrap().success());
    assert!(!runtime.vm_process_marker.exists());
}

#[test]
fn recovery_requires_positive_evidence_of_guest_presence_or_absence() {
    assert!(registered_guest(false, b"", "LemmaRuntime").is_err());
    assert!(registered_guest(false, b"LemmaRuntime", "LemmaRuntime").is_err());
    assert!(!registered_guest(true, b"Ubuntu\r\nLemmaRuntime-dev\r\n", "LemmaRuntime").unwrap());
    assert!(registered_guest(true, b"Ubuntu\r\nLemmaRuntime\r\n", "LemmaRuntime").unwrap());
    let utf16: Vec<u8> = "LemmaRuntime\r\n"
        .encode_utf16()
        .flat_map(u16::to_le_bytes)
        .collect();
    assert!(registered_guest(true, &utf16, "LemmaRuntime").unwrap());
}
