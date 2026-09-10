//! The shared secret the host and guest authenticate with.

use super::*;

#[test]
fn capability_is_stable_private_and_not_in_command_arguments() {
    let root = tempdir().unwrap();
    let artifacts = root.path().join("artifacts/macos-aarch64");
    fs::create_dir_all(&artifacts).unwrap();
    let config = ManagedRuntimeConfig {
        wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
        local_root: root.path().join("local"),
        artifact_root: root.path().join("artifacts"),
        bridge_executable: root.path().join("lemma-runtime"),
        #[cfg(target_os = "macos")]
        vz_executable: root.path().join("lemma-vz"),
        #[cfg(windows)]
        wsl_executable: PathBuf::from("wsl.exe"),
    };
    let runtime = ManagedRuntime::new(config).unwrap();
    runtime.ensure_capability().unwrap();
    let first = fs::read_to_string(runtime.capability_file()).unwrap();
    runtime.ensure_capability().unwrap();

    assert_eq!(first.len(), 64);
    assert_eq!(
        first,
        fs::read_to_string(runtime.capability_file()).unwrap()
    );
    ensure_private_file(runtime.capability_file()).unwrap();
}
