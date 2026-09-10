//! Which address the guest reports, and what a callback failure means.

use super::*;

/// A failed callback has to say which side of it is broken.
///
/// Both readings end with a sandbox that cannot reach the API, and the
/// remedies have nothing in common: one is a permission or a VPN on this
/// computer, the other is Lemma's own relay. The guest already knew the
/// answer -- `diagnostics.network` -- and nothing asked it.
#[test]
fn a_failed_callback_distinguishes_the_route_from_the_network() {
    let relay = callback_failure_message(Some("connection refused"), true);
    assert!(relay.contains("connection refused"), "{relay}");
    assert!(
        relay.contains("route back to this computer"),
        "guest egress working points at the host's relay: {relay}"
    );

    let network = callback_failure_message(None, false);
    assert!(
        network.contains("probe timed out"),
        "an absent cause still needs describing: {network}"
    );
    assert!(
        network.contains("network is unavailable"),
        "no name resolution at all is a different problem: {network}"
    );
    assert!(
        !network.contains("route back to this computer"),
        "the two readings must not both appear: {network}"
    );
}

/// A guest with no DHCP lease still serves.
///
/// The lease comes from vmnet, which is exactly what macOS Local Network
/// privacy withholds from the responsible app. `discover` used to refuse to
/// start without one, so a denied permission put guestd into a systemd
/// restart loop: the vsock control port never listened, the host waited out
/// its two minutes and said "managed guest did not become ready", and the
/// private service bridges -- which need no lease at all -- never got the
/// chance to work. The app looked dead for a permission that only affects
/// reaching sandboxes.
#[test]
fn a_guest_without_a_network_lease_still_reports_ready() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(vec![output(true, "")]),
        root.path().into(),
        None,
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let response = service.handle(GuestRequest {
        version: 1,
        operation: "health".into(),
        parameters: json!({}),
        capability: None,
    });

    assert!(response.ok, "{:?}", response.error);
    let result = response.result.unwrap();
    assert_eq!(result["status"], "ready");
    assert_eq!(
        result["endpoint_host"],
        Value::Null,
        "an address it does not have must not be invented"
    );
    assert_eq!(
        result["network"]["leased"], false,
        "the host has to be able to tell this apart from a guest that died"
    );
}

/// What the missing lease actually costs, said by name.
#[test]
fn without_a_lease_only_sandbox_addresses_are_refused() {
    let root = tempdir().unwrap();
    let service = GuestService::new(
        FakeEngine::new(Vec::new()),
        root.path().into(),
        None,
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    let error = service.routable_endpoint_host().unwrap_err();
    assert_eq!(error.code, "guest_network_unavailable");
    assert!(
        error.retryable,
        "granting the permission fixes it, so this must not be terminal"
    );
    assert!(
        error.message.contains("Local Network"),
        "the message has to name the cause somebody can act on: {}",
        error.message
    );
}

/// Readiness must not depend on the lease either.
///
/// The core containers run with host networking, so they answer on
/// loopback in the guest's own namespace. Probing them through the
/// routable address is what tied startup to a permission that has nothing
/// to do with whether Postgres came up.
#[test]
fn core_readiness_probes_do_not_need_a_routable_address() {
    let root = tempdir().unwrap();
    // A stand-in for a core container: host networking means it answers on
    // every address in the guest's namespace, loopback included.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();

    let service = GuestService::new(
        FakeEngine::new(Vec::new()),
        root.path().into(),
        None, // No DHCP lease, as when Local Network is denied.
        "192.168.64.1".into(),
        None,
    )
    .unwrap();

    service
        .wait_tcp(port, 5)
        .expect("readiness must not depend on a lease it may never get");
}

/// WSL reports a loopback address first, and the host cannot reach it.
///
/// `ip -4 -o addr show scope global` lists `lo` before `eth0` on WSL,
/// because WSL assigns `10.255.255.254/32` to `lo` for its own DNS relay
/// and marks it global. Taking the first address in that output therefore
/// told the Windows host to connect to the guest's loopback, and every
/// connection timed out -- while the services it wanted were listening on
/// `eth0` and, through WSL's own forwarding, on the host's `127.0.0.1`.
///
/// The Windows fixture is verbatim from that guest as it failed. The
/// macOS one is not a capture: a VZ guest has no exec channel, so it was
/// assembled, and then checked line by line against the guest it stands
/// for. Its field values -- `enp0s1`, index 2, `/24`, the broadcast, the
/// global scope, the DHCP lease that makes it `dynamic`, and that `lo`
/// carries no global IPv4 so cannot appear -- were read off the running
/// guest through the one namespace reachable from the host. Its exact
/// bytes, which are a property of iproute2 rather than of the guest --
/// four spaces after the name, the `\` and the seven that follow it --
/// were reproduced by running the same iproute2 the guest ships, from the
/// same Ubuntu 24.04, over a dummy `enp0s1` given that address and lease.
/// The two agree character for character apart from the index, and the
/// index is the part that was read from the guest.
#[test]
fn the_guest_reports_an_address_the_host_can_actually_reach() {
    let wsl = "\
1: lo    inet 10.255.255.254/32 brd 10.255.255.254 scope global lo\\       valid_lft forever preferred_lft forever
2: eth0    inet 172.24.27.216/20 brd 172.24.31.255 scope global eth0\\       valid_lft forever preferred_lft forever
";
    assert_eq!(
        first_reachable_address(wsl).as_deref(),
        Some("172.24.27.216"),
        "an address on lo is not somewhere another machine can reach it"
    );

    let vz = "2: enp0s1    inet 192.168.64.10/24 brd 192.168.64.255 scope global dynamic enp0s1\\       valid_lft 2591990sec preferred_lft 2591990sec\n";
    assert_eq!(
        first_reachable_address(vz).as_deref(),
        Some("192.168.64.10")
    );
}

/// A veth's name carries its peer index, and the name is the part before it.
#[test]
fn an_interface_named_after_its_peer_is_still_read_correctly() {
    let veth = "3: eth0@if12    inet 10.4.0.7/24 brd 10.4.0.255 scope global eth0\n";
    assert_eq!(first_reachable_address(veth).as_deref(), Some("10.4.0.7"));
}

#[test]
fn a_guest_with_only_loopback_reports_no_address_at_all() {
    // Which is what `endpoint_host: None` is for: a guest with no lease is
    // still healthy, it just cannot be reached from outside.
    let only_loopback = "1: lo    inet 10.255.255.254/32 brd 10.255.255.254 scope global lo\n";
    assert_eq!(first_reachable_address(only_loopback), None);
    assert_eq!(first_reachable_address(""), None);
}
