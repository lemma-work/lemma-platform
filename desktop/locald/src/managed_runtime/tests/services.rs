//! Waiting for the private services, and saying which one did not answer.

use super::*;

#[test]
fn managed_endpoints_must_stay_on_private_ipv4() {
    assert_eq!(
        private_ipv4("192.168.64.2", "guest").unwrap(),
        Ipv4Addr::new(192, 168, 64, 2)
    );
    assert!(private_ipv4("127.0.0.1", "guest").is_err());
    assert!(private_ipv4("8.8.8.8", "guest").is_err());
    assert!(private_ipv4("::1", "guest").is_err());
}

#[test]
fn private_service_gate_waits_for_every_endpoint() {
    let first = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let second = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let services = [
        ("first", first.local_addr().unwrap().port()),
        ("second", second.local_addr().unwrap().port()),
    ];

    wait_for_tcp_services(
        Ipv4Addr::LOCALHOST,
        &services,
        Duration::from_millis(250),
        || Ok(()),
    )
    .unwrap();
}

#[test]
fn private_service_gate_honors_cancellation_before_connecting() {
    let service = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    service.set_nonblocking(true).unwrap();
    let error = wait_for_tcp_services(
        Ipv4Addr::LOCALHOST,
        &[("Redis", service.local_addr().unwrap().port())],
        Duration::from_secs(30),
        || Err(io::Error::new(io::ErrorKind::Interrupted, "cancelled")),
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::Interrupted);
    assert_eq!(
        service.accept().unwrap_err().kind(),
        io::ErrorKind::WouldBlock
    );
}

#[test]
fn private_service_gate_cancellation_interrupts_an_unfinished_wait() {
    use std::cell::Cell;
    let service = crate::port_reservation::PortReservation::ephemeral().unwrap();
    let port = service.port();
    let checks = Cell::new(0);
    let error = wait_for_tcp_services(
        Ipv4Addr::LOCALHOST,
        &[("Redis", port)],
        Duration::from_secs(30),
        || {
            checks.set(checks.get() + 1);
            if checks.get() >= 3 {
                Err(io::Error::new(io::ErrorKind::Interrupted, "cancelled"))
            } else {
                Ok(())
            }
        },
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::Interrupted);
}

#[test]
fn private_service_gate_reports_only_unreachable_services() {
    let ready = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
    let unavailable = crate::port_reservation::PortReservation::ephemeral().unwrap();
    let unavailable_port = unavailable.port();
    let error = wait_for_tcp_services(
        Ipv4Addr::LOCALHOST,
        &[
            ("PostgreSQL", ready.local_addr().unwrap().port()),
            ("Redis", unavailable_port),
        ],
        Duration::from_secs(1),
        || Ok(()),
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    // A bound, non-listening socket can refuse or time out by platform.
    assert!(error.to_string().contains("Redis:"), "{error}");
    assert!(!error.to_string().contains("PostgreSQL"));
    assert!(error.to_string().contains("stored data has not been reset"));
}

#[test]
fn private_connectivity_reasons_do_not_misdiagnose_route_failure_as_permission_denial() {
    assert_eq!(
        private_connection_reason(io::ErrorKind::ConnectionRefused),
        "service not accepting connections"
    );
    assert_eq!(
        private_connection_reason(io::ErrorKind::PermissionDenied),
        "connection denied"
    );
    assert_eq!(
        private_connection_reason(io::ErrorKind::HostUnreachable),
        "network route unavailable"
    );
    assert_eq!(
        private_connection_reason(io::ErrorKind::NetworkUnreachable),
        "network route unavailable"
    );
    assert_eq!(
        private_connection_reason(io::ErrorKind::TimedOut),
        "connection timed out"
    );
}

#[test]
fn host_processes_use_private_guest_services_without_published_infra_ports() {
    let root = tempdir().unwrap();
    let controller = ManagedRuntimeController {
        runtime: ManagedRuntime::new(ManagedRuntimeConfig {
            wsl_distribution: "LemmaRuntime-separate-installation".to_string(),
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

    let environment = controller.backend_environment().unwrap();
    let (database, auth) = if cfg!(target_os = "macos") {
        ("@127.0.0.1:55432/lemma", "http://127.0.0.1:53567")
    } else {
        ("@192.168.64.10:5432/lemma", "http://192.168.64.10:3567")
    };
    assert!(environment["DATABASE_URL"].contains(database));
    assert_eq!(environment["SUPERTOKENS_CORE_URL"], auth);
    assert!(Path::new(&environment["LEMMA_GUEST_CAPABILITY_FILE"])
        .ends_with("local/run/guest-control/guest.capability"));
    assert!(Path::new(&environment["LEMMA_GUEST_CONTROL_SOCKET"]).ends_with("local/run/guest.sock"));
    assert_eq!(
        environment["LEMMA_WSL_DISTRIBUTION"],
        "LemmaRuntime-separate-installation"
    );
    assert_eq!(
        environment.values().any(|value| value.contains(":55432")),
        cfg!(target_os = "macos")
    );
}
