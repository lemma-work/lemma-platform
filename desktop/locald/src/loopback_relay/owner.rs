//! Who is listening on a loopback port, and whether the Agent Host started it.
//!
//! The relay exists so the paired user's agent can check a server *it*
//! started on this Mac. A deny list of Lemma's own ports left everything else
//! on the Mac's loopback in reach -- a database the person runs, a local
//! admin console, a password manager's helper -- for every process in the
//! workspace sandbox, including runs nobody at this Mac started. So the relay
//! connects only to a port whose listener descends from the Agent Host
//! process locald supervises: the exec-server and the coding agents it runs
//! are its children, and so is anything they start.
//!
//! macOS has no `/proc`. `libproc` answers both questions: every process's
//! open sockets (`PROC_PIDLISTFDS`, then `PROC_PIDFDSOCKETINFO` for each), and
//! each process's parent (`PROC_PIDTBSDINFO`). Only this user's processes can
//! be inspected, which is also the only user whose processes the Agent Host
//! could have started.
//!
//! What this cannot see: a server that detached itself (`nohup … &` in a
//! shell that then exited, a daemon that double-forks) is reparented to
//! `launchd` and no longer descends from anything. It is refused, and the
//! refusal says why.

use std::collections::BTreeMap;

/// One listening TCP socket on the port asked about.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Listener {
    pub(crate) pid: u32,
}

/// The processes listening on `port` on an address loopback reaches -- a
/// loopback address or a wildcard -- as far as this user can see.
pub(crate) type ListenersOn = dyn Fn(u16) -> Vec<Listener> + Send + Sync;
/// A process's parent, or `None` once there is none to follow.
pub(crate) type ParentOf = dyn Fn(u32) -> Option<u32> + Send + Sync;

/// How far up a process tree is followed before giving up. Real trees are a
/// handful deep; this only bounds a loop in a lying `parent_of`.
const MAX_ANCESTRY: usize = 64;

/// Whether `pid` is `root` or one of its descendants.
pub(crate) fn descends_from(pid: u32, root: u32, parent_of: &ParentOf) -> bool {
    let mut current = pid;
    for _ in 0..MAX_ANCESTRY {
        if current == root {
            return true;
        }
        match parent_of(current) {
            Some(parent) if parent > 1 && parent != current => current = parent,
            _ => return false,
        }
    }
    false
}

/// Why a port's listener was not accepted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum NotOwned {
    /// No Agent Host is running, so nothing on this Mac is the agent's.
    NoAgentHost,
    /// Nothing this user runs is listening there.
    NoListener,
    /// Something is, and at least one of them was not started by the agent.
    NotTheAgents,
}

/// Every listener on `port` must descend from `root`, and there must be one.
///
/// *Every*, not *any*: two processes can listen on one port (`127.0.0.1` and
/// `::1`, or a wildcard beside a specific address), and the relay cannot
/// choose which of them its connection lands on.
pub(crate) fn agent_owns(
    port: u16,
    root: Option<u32>,
    listeners_on: &ListenersOn,
    parent_of: &ParentOf,
) -> Result<(), NotOwned> {
    let root = root.ok_or(NotOwned::NoAgentHost)?;
    let listeners = listeners_on(port);
    if listeners.is_empty() {
        return Err(NotOwned::NoListener);
    }
    // One walk per process, however many sockets it has on the port.
    let mut verdicts = BTreeMap::new();
    for listener in listeners {
        let owned = *verdicts
            .entry(listener.pid)
            .or_insert_with(|| descends_from(listener.pid, root, parent_of));
        if !owned {
            return Err(NotOwned::NotTheAgents);
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
pub(crate) use macos::{listeners_on, parent_of};

#[cfg(not(target_os = "macos"))]
pub(crate) fn listeners_on(_port: u16) -> Vec<Listener> {
    Vec::new()
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn parent_of(_pid: u32) -> Option<u32> {
    None
}

#[cfg(target_os = "macos")]
mod macos {
    //! `libproc`, as `lsof` uses it.
    //!
    //! `struct socket_fdinfo` is not in the `libc` crate, and is a union of a
    //! dozen protocol records, so rather than transcribe all of it the fields
    //! needed are read at their offsets in `<sys/proc_info.h>`. The layout has
    //! been ABI-stable since 10.5 (`lsof` compiles against it unchanged), and
    //! `tests::a_socket_of_our_own_is_found_where_we_bound_it` checks every
    //! offset against a real listener on every run.

    use super::Listener;
    use std::mem::{size_of, MaybeUninit};

    const PROC_PIDFDSOCKETINFO: libc::c_int = 3;
    const SOCKINFO_TCP: i32 = 2;
    const TSI_S_LISTEN: i32 = 1;
    const INI_IPV4: u8 = 0x1;
    const INI_IPV6: u8 = 0x2;

    // `struct socket_fdinfo`: `proc_fileinfo` (24 bytes), then `socket_info`,
    // whose `soi_kind` sits after the 136-byte `vinfo_stat`, two 64-bit ids,
    // the socket's shorts and its two `sockbuf_info`s, and whose protocol
    // union follows at the next 8-byte boundary.
    const SOI_KIND: usize = 24 + 232;
    const SOI_PROTO: usize = 24 + 240;
    // Within the union, `tcp_sockinfo` opens with its `in_sockinfo`.
    const INSI_LPORT: usize = SOI_PROTO + 4;
    const INSI_VFLAG: usize = SOI_PROTO + 24;
    const INSI_LADDR: usize = SOI_PROTO + 48;
    const TCPSI_STATE: usize = SOI_PROTO + 80;
    /// Larger than `struct socket_fdinfo` (under 800 bytes); the kernel
    /// writes exactly its size and says so.
    const BUFFER_WORDS: usize = 256;

    fn read_i32(buffer: &[u8], offset: usize) -> i32 {
        i32::from_ne_bytes(buffer[offset..offset + 4].try_into().expect("four bytes"))
    }

    fn all_pids() -> Vec<libc::pid_t> {
        // SAFETY: a null buffer asks for the count; the second call writes at
        // most `capacity` pids into a buffer of that many.
        unsafe {
            let count = libc::proc_listallpids(std::ptr::null_mut(), 0);
            if count <= 0 {
                return Vec::new();
            }
            // Room for processes started between the two calls.
            let capacity = count as usize + 64;
            let mut pids: Vec<libc::pid_t> = vec![0; capacity];
            let written = libc::proc_listallpids(
                pids.as_mut_ptr().cast(),
                (capacity * size_of::<libc::pid_t>()) as libc::c_int,
            );
            pids.truncate(written.max(0) as usize);
            pids
        }
    }

    fn socket_fds(pid: libc::pid_t) -> Vec<i32> {
        // SAFETY: sized calls into a buffer of `proc_fdinfo` records; a
        // process this user may not inspect answers 0 and is skipped.
        unsafe {
            let bytes = libc::proc_pidinfo(pid, libc::PROC_PIDLISTFDS, 0, std::ptr::null_mut(), 0);
            if bytes <= 0 {
                return Vec::new();
            }
            let capacity = bytes as usize / size_of::<libc::proc_fdinfo>() + 16;
            let mut fds: Vec<libc::proc_fdinfo> = Vec::with_capacity(capacity);
            let written = libc::proc_pidinfo(
                pid,
                libc::PROC_PIDLISTFDS,
                0,
                fds.as_mut_ptr().cast(),
                (capacity * size_of::<libc::proc_fdinfo>()) as libc::c_int,
            );
            if written <= 0 {
                return Vec::new();
            }
            fds.set_len((written as usize / size_of::<libc::proc_fdinfo>()).min(capacity));
            fds.iter()
                .filter(|fd| fd.proc_fdtype as libc::c_int == libc::PROX_FDTYPE_SOCKET)
                .map(|fd| fd.proc_fd)
                .collect()
        }
    }

    /// The local port of `fd` in `pid` if it is a listening TCP socket on an
    /// address loopback reaches.
    fn listening_port(pid: libc::pid_t, fd: i32) -> Option<u16> {
        let mut words = [0_u64; BUFFER_WORDS];
        // SAFETY: an 8-byte-aligned buffer larger than `socket_fdinfo`; the
        // kernel reports how much of it it wrote and nothing past that is read.
        let written = unsafe {
            libc::proc_pidfdinfo(
                pid,
                fd,
                PROC_PIDFDSOCKETINFO,
                words.as_mut_ptr().cast(),
                (BUFFER_WORDS * 8) as libc::c_int,
            )
        };
        if written <= 0 || (written as usize) < TCPSI_STATE + 4 {
            return None;
        }
        // SAFETY: plain bytes of the buffer the kernel just filled.
        let buffer: &[u8] =
            unsafe { std::slice::from_raw_parts(words.as_ptr().cast(), written as usize) };
        if read_i32(buffer, SOI_KIND) != SOCKINFO_TCP
            || read_i32(buffer, TCPSI_STATE) != TSI_S_LISTEN
        {
            return None;
        }
        let vflag = buffer[INSI_VFLAG];
        let address: [u8; 16] = buffer[INSI_LADDR..INSI_LADDR + 16]
            .try_into()
            .expect("sixteen bytes");
        let reachable = if vflag & INI_IPV6 != 0 {
            let v6 = std::net::Ipv6Addr::from(address);
            v6.is_unspecified()
                || v6.is_loopback()
                || v6
                    .to_ipv4_mapped()
                    .is_some_and(|v4| v4.is_loopback() || v4.is_unspecified())
        } else if vflag & INI_IPV4 != 0 {
            // `in4in6_addr`: three words of padding, then the address.
            let v4 = std::net::Ipv4Addr::new(address[12], address[13], address[14], address[15]);
            v4.is_unspecified() || v4.is_loopback()
        } else {
            false
        };
        if !reachable {
            return None;
        }
        // Stored in network byte order in the low half of an `int`.
        Some(u16::from_be(read_i32(buffer, INSI_LPORT) as u16))
    }

    pub(crate) fn listeners_on(port: u16) -> Vec<Listener> {
        let mut found = Vec::new();
        for pid in all_pids().into_iter().filter(|pid| *pid > 0) {
            for fd in socket_fds(pid) {
                if listening_port(pid, fd) == Some(port) {
                    found.push(Listener { pid: pid as u32 });
                }
            }
        }
        found
    }

    pub(crate) fn parent_of(pid: u32) -> Option<u32> {
        let mut info = MaybeUninit::<libc::proc_bsdinfo>::zeroed();
        let size = size_of::<libc::proc_bsdinfo>() as libc::c_int;
        // SAFETY: a zeroed `proc_bsdinfo` of exactly the size asked for.
        let written = unsafe {
            libc::proc_pidinfo(
                pid as libc::c_int,
                libc::PROC_PIDTBSDINFO,
                0,
                info.as_mut_ptr().cast(),
                size,
            )
        };
        if written != size {
            return None;
        }
        // SAFETY: the kernel filled all of it.
        let info = unsafe { info.assume_init() };
        Some(info.pbi_ppid)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn tree(edges: &[(u32, u32)]) -> impl Fn(u32) -> Option<u32> {
        let parents: HashMap<u32, u32> = edges.iter().copied().collect();
        move |pid| parents.get(&pid).copied()
    }

    #[test]
    fn a_descendant_of_the_agent_host_is_its_own() {
        // launchd(1) -> locald(100) -> agent host(200) -> sandbox-exec(300)
        // -> exec-server(400) -> sh(500) -> node(600); and a stranger(700).
        let parent_of = tree(&[
            (100, 1),
            (200, 100),
            (300, 200),
            (400, 300),
            (500, 400),
            (600, 500),
            (700, 1),
        ]);
        assert!(descends_from(600, 200, &parent_of));
        assert!(descends_from(200, 200, &parent_of));
        assert!(!descends_from(700, 200, &parent_of));
        // locald is the Agent Host's parent, not its child.
        assert!(!descends_from(100, 200, &parent_of));
        // A process whose parent cannot be read is nobody's.
        assert!(!descends_from(999, 200, &parent_of));
    }

    #[test]
    fn every_listener_on_the_port_must_be_the_agents() {
        let parent_of = tree(&[(200, 100), (600, 200), (700, 1)]);
        let listeners = |pids: Vec<u32>| {
            move |_port: u16| -> Vec<Listener> {
                pids.iter().map(|pid| Listener { pid: *pid }).collect()
            }
        };
        assert_eq!(
            agent_owns(3000, Some(200), &listeners(vec![600, 600]), &parent_of),
            Ok(())
        );
        assert_eq!(
            agent_owns(5432, Some(200), &listeners(vec![700]), &parent_of),
            Err(NotOwned::NotTheAgents),
            "a server the person runs themselves"
        );
        assert_eq!(
            agent_owns(3000, Some(200), &listeners(vec![600, 700]), &parent_of),
            Err(NotOwned::NotTheAgents),
            "the relay cannot pick which of two listeners it reaches"
        );
        assert_eq!(
            agent_owns(3000, Some(200), &listeners(vec![]), &parent_of),
            Err(NotOwned::NoListener)
        );
        assert_eq!(
            agent_owns(3000, None, &listeners(vec![600]), &parent_of),
            Err(NotOwned::NoAgentHost)
        );
    }

    /// The offsets into `socket_fdinfo`, against the kernel: a listener this
    /// process opens is found on its port and attributed to this process, a
    /// connected socket is not a listener, and a child's listener is the
    /// child's.
    #[cfg(unix)]
    #[cfg(target_os = "macos")]
    #[test]
    fn a_socket_of_our_own_is_found_where_we_bound_it() {
        let ours = std::process::id();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(
            listeners_on(port).contains(&Listener { pid: ours }),
            "our own listener on {port} was not found: {:?}",
            listeners_on(port)
        );
        let _client = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        assert!(
            listeners_on(port).iter().all(|found| found.pid == ours),
            "a connected socket was taken for a listener"
        );
        drop(listener);

        if let Ok(v6) = std::net::TcpListener::bind("[::1]:0") {
            let port = v6.local_addr().unwrap().port();
            assert!(listeners_on(port).contains(&Listener { pid: ours }));
        }

        // A child's listener, attributed to the child.
        let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let child_port = probe.local_addr().unwrap().port();
        drop(probe);
        let mut child = std::process::Command::new("/usr/bin/nc")
            .args(["-l", "127.0.0.1", &child_port.to_string()])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut found = Vec::new();
        while std::time::Instant::now() < deadline {
            found = listeners_on(child_port);
            if !found.is_empty() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        let descends = descends_from(child.id(), ours, &parent_of);
        let _ = child.kill();
        let _ = child.wait();
        assert_eq!(found, vec![Listener { pid: child.id() }]);
        assert!(descends, "a child's parent walk reaches us");
        assert!(!descends_from(ours, child.id(), &parent_of));
    }
}
