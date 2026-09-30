//! The host DNS relay's guest end: what it hands the host, what it answers
//! when the host does not, and the guest image config that points resolved
//! at it. Unix socket pairs stand in for vsock.

use super::*;
use crate::host_dns::{
    address_query, answers_address, fit_udp, forward, serve_tcp, serve_udp, server_failure,
    udp_answer_limit, MAX_QUERY_BYTES,
};
use std::net::{TcpListener, UdpSocket};
use std::os::unix::net::UnixStream;

const DOCKER_HUB: &str = "registry-1.docker.io";

fn rcode(message: &[u8]) -> u8 {
    message[3] & 0x0F
}

/// What the Mac's resolver would say: one A record for the question.
fn answer_for(query: &[u8]) -> Vec<u8> {
    let question_end = question_end_of(query);
    let mut answer = query[..2].to_vec();
    answer.extend_from_slice(&[0x81, 0x80, 0, 1, 0, 1, 0, 0, 0, 0]);
    answer.extend_from_slice(&query[12..question_end]);
    answer.extend_from_slice(&[0xC0, 0x0C, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4, 192, 0, 2, 7]);
    answer
}

fn question_end_of(query: &[u8]) -> usize {
    let mut at = 12;
    while query[at] != 0 {
        at += usize::from(query[at]) + 1;
    }
    at + 5
}

/// A stand-in host on the far end of a socket pair: reads one framed query,
/// hands it to `respond`, and writes back whatever that returns, framed.
fn host(respond: fn(&[u8]) -> Option<Vec<u8>>) -> io::Result<UnixStream> {
    let (guest_end, mut host) = UnixStream::pair()?;
    guest_end.set_read_timeout(Some(Duration::from_millis(300)))?;
    host.set_read_timeout(Some(Duration::from_secs(5)))?;
    thread::spawn(move || {
        let mut length = [0_u8; 2];
        host.read_exact(&mut length).unwrap();
        let mut query = vec![0_u8; usize::from(u16::from_be_bytes(length))];
        host.read_exact(&mut query).unwrap();
        if let Some(answer) = respond(&query) {
            let mut framed = (answer.len() as u16).to_be_bytes().to_vec();
            framed.extend_from_slice(&answer);
            let _ = host.write_all(&framed);
        } else {
            // Say nothing, and keep the stream open past the guest's timeout.
            thread::sleep(Duration::from_secs(1));
        }
    });
    Ok(guest_end)
}

#[test]
fn a_query_goes_to_the_host_framed_and_its_answer_comes_back() {
    let query = address_query(DOCKER_HUB, 7);
    let answer = forward(&query, || host(|query| Some(answer_for(query)))).unwrap();
    assert_eq!(answer, answer_for(&query));
    assert_eq!(rcode(&answer), 0);
}

/// SERVFAIL is the one answer resolved does not believe: it asks the gateway.
#[test]
fn a_host_that_does_not_answer_in_time_is_server_failure() {
    let query = address_query(DOCKER_HUB, 7);
    let started = Instant::now();
    let answer = forward(&query, || host(|_| None)).unwrap();
    assert!(started.elapsed() < Duration::from_secs(1));
    assert_eq!(rcode(&answer), 2);
    assert_eq!(answer[..2], query[..2]);
    assert_eq!(answer[2] & 0x80, 0x80, "a response");
    // The question is echoed, so resolved can match the failure to its query.
    assert_eq!(&answer[12..], &query[12..]);
}

#[test]
fn an_unreachable_host_or_a_foreign_answer_is_server_failure() {
    let query = address_query(DOCKER_HUB, 7);
    let refused = forward(&query, || -> io::Result<UnixStream> {
        Err(io::ErrorKind::ConnectionRefused.into())
    });
    assert_eq!(rcode(&refused.unwrap()), 2);

    let wrong_id = forward(&query, || {
        host(|query| {
            let mut answer = answer_for(query);
            answer[1] ^= 0xFF;
            Some(answer)
        })
    });
    assert_eq!(rcode(&wrong_id.unwrap()), 2);

    let too_short = forward(&query, || host(|query| Some(query[..6].to_vec())));
    assert_eq!(rcode(&too_short.unwrap()), 2);
}

#[test]
fn an_oversized_query_is_never_sent_and_garbage_gets_no_answer() {
    let mut query = address_query(DOCKER_HUB, 7);
    query.resize(MAX_QUERY_BYTES + 1, 0);
    let answer = forward(&query, || -> io::Result<UnixStream> {
        panic!("an oversized query reached the host")
    });
    assert_eq!(rcode(&answer.unwrap()), 2);

    // Nothing with no id to answer, and never an answer to an answer.
    assert_eq!(server_failure(&[1, 2, 3]), None);
    assert_eq!(
        server_failure(&answer_for(&address_query(DOCKER_HUB, 7))),
        None
    );
}

#[test]
fn an_answer_too_large_for_udp_is_truncated_so_the_client_retries_over_tcp() {
    let query = address_query(DOCKER_HUB, 7);
    assert_eq!(udp_answer_limit(&query), 512);
    let mut large = answer_for(&query);
    large.resize(900, 0);
    let fitted = fit_udp(large.clone(), &query);
    assert_eq!(fitted[2] & 0x02, 0x02, "TC set");
    assert_eq!(fitted[6..12], [0; 6]);
    assert_eq!(&fitted[12..], &query[12..]);

    // A client that advertised EDNS gets what it asked for.
    let mut edns = query.clone();
    edns[11] = 1;
    edns.extend_from_slice(&[0, 0, 41, 0x10, 0x00, 0, 0, 0, 0, 0, 0]);
    assert_eq!(udp_answer_limit(&edns), 4096);
    assert_eq!(fit_udp(large.clone(), &edns), large);
}

fn udp_relay(dial: fn() -> io::Result<UnixStream>) -> std::net::SocketAddr {
    let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
    let address = socket.local_addr().unwrap();
    thread::spawn(move || serve_udp(socket, Arc::new(dial)));
    address
}

#[test]
fn over_udp_the_relay_resolves_what_the_host_resolves() {
    let working = udp_relay(|| host(|query| Some(answer_for(query))));
    assert!(answers_address(working, DOCKER_HUB, Duration::from_secs(5)));

    let failing = udp_relay(|| Err(io::ErrorKind::ConnectionRefused.into()));
    assert!(!answers_address(
        failing,
        DOCKER_HUB,
        Duration::from_secs(5)
    ));
}

#[test]
fn over_tcp_one_connection_carries_several_queries() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    thread::spawn(move || serve_tcp(listener, Arc::new(|| host(|q| Some(answer_for(q))))));
    let mut client = TcpStream::connect(address).unwrap();
    client
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    for id in [1_u16, 2] {
        let query = address_query(DOCKER_HUB, id);
        let mut framed = (query.len() as u16).to_be_bytes().to_vec();
        framed.extend_from_slice(&query);
        client.write_all(&framed).unwrap();
        let mut length = [0_u8; 2];
        client.read_exact(&mut length).unwrap();
        let mut answer = vec![0_u8; usize::from(u16::from_be_bytes(length))];
        client.read_exact(&mut answer).unwrap();
        assert_eq!(answer, answer_for(&query));
    }
}

/// The relay first, the gateway second, and nothing public anywhere.
///
/// The relay is resolved's global server; the gateway reaches it per link,
/// from DHCP. Disabling DHCP's DNS would leave a guest whose relay is down
/// with no DNS at all, and a public fallback would bypass split DNS.
#[test]
fn the_guest_image_asks_the_relay_first_and_keeps_the_gateway() {
    let resolved =
        include_str!("../../../guest-image/rootfs-overlay/etc/systemd/resolved.conf.d/lemma.conf");
    let settings: Vec<&str> = resolved
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .collect();
    assert_eq!(settings, ["[Resolve]", "DNS=127.0.0.2", "FallbackDNS="]);
    assert_eq!(
        crate::host_dns::HOST_DNS_ADDRESS,
        "127.0.0.2:53",
        "resolved and guestd must name the same address"
    );

    let network =
        include_str!("../../../guest-image/rootfs-overlay/etc/systemd/network/20-lemma.network");
    assert!(network.lines().any(|line| line.trim() == "DHCP=yes"));
    assert!(
        !network.contains("UseDNS=no") && !network.contains("UseDNS=false"),
        "the gateway's DNS is the fallback when the relay is down"
    );
    assert!(!network
        .lines()
        .any(|line| line.trim_start().starts_with("DNS=")));

    // Every lookup goes through resolved, so both servers are always in play.
    let build = include_str!("../../../../../scripts/build_local_guest_runtime.sh");
    assert!(build.contains("ln -s ../run/systemd/resolve/stub-resolv.conf /rootfs/etc/resolv.conf"));
}

#[test]
fn the_vsock_port_is_the_one_after_the_loopback_relay() {
    assert_eq!(
        crate::HOST_DNS_VSOCK_PORT,
        crate::HOST_LOOPBACK_VSOCK_PORT + 1
    );
    let helper = include_str!("../../../macos-vz/Sources/LemmaVZ/main.swift");
    assert!(helper.contains("private let hostDNSPort: UInt32 = 42_414"));
}
