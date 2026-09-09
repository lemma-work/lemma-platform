//! The managed runtime's guards, grouped the way the code they cover is
//! grouped.

mod clock;
mod forwarders;
mod images;
mod probe;
mod secrets;
mod services;

use super::*;
use std::io::Read;
use std::net::TcpListener;
use std::sync::mpsc;
use tempfile::tempdir;

/// A controller with no VM behind it. Enough for anything that only reads
/// or writes the controller's own state.
pub(super) fn test_controller() -> (tempfile::TempDir, ManagedRuntimeController) {
    let root = tempdir().unwrap();
    let controller = ManagedRuntimeController {
        runtime: ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: DEFAULT_WSL_DISTRIBUTION.to_string(),
            local_root: root.path().join("local"),
            artifact_root: root.path().join("artifacts"),
            bridge_executable: root.path().join("lemma-runtime"),
            #[cfg(target_os = "macos")]
            vz_executable: root.path().join("lemma-vz"),
            #[cfg(windows)]
            wsl_executable: PathBuf::from("wsl.exe"),
        })
        .unwrap(),
        spec: ManagedRuntimeSpec {
            images: crate::host_process::ManagedRuntimeImages {
                postgres: "postgres@sha256:test".into(),
                redis: "redis@sha256:test".into(),
                supertokens: "supertokens@sha256:test".into(),
                workspace: Some("workspace@sha256:test".into()),
                function: Some("function@sha256:test".into()),
            },
            credentials: crate::host_process::ManagedRuntimeCredentials {
                postgres_password: "a".repeat(64),
                redis_password: "b".repeat(64),
            },
            ports: crate::host_process::ManagedRuntimePorts {
                postgres: 55432,
                redis: 56379,
                supertokens: 53567,
                backend: 8711,
                frontend: 3711,
            },
        },
        forwarders: Mutex::new(Vec::new()),
        probes: Mutex::new(ProbeTracker::default()),
        clock_keeper: Mutex::new(None),
        last_clock_error: Mutex::new(None),
        sandbox_images: Mutex::new(SandboxImageStatus::default()),
        pending_auth: Mutex::new(None),
        pending_images: Mutex::new(None),
        cancellation: lemma_desktop_process::Cancellation::default(),
        status: Mutex::new(Some(ManagedRuntimeStatus {
            endpoint_host: Some("192.168.64.10".into()),
            host_gateway: "192.168.64.1".into(),
            engine: "containerd".into(),
            active_sandboxes: 0,
            balloon_state: None,
            balloon_target_bytes: None,
        })),
    };

    (root, controller)
}
