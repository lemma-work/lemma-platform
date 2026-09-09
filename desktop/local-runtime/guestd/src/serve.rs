//! Accepting connections over vsock.

use super::*;

#[cfg(target_os = "linux")]
pub fn serve_vsock<E: Engine + 'static>(service: &GuestService<E>) -> io::Result<()> {
    // Named rather than reached through the crate root: this is the only
    // caller outside `protocol`, and only on Linux, so a re-export at the
    // root would be dead code on every other host -- which `-D warnings`
    // makes an error rather than a warning.
    use crate::protocol::handle_stream;
    use std::mem::{size_of, zeroed};
    use std::os::fd::{FromRawFd, OwnedFd};
    use std::sync::atomic::{AtomicUsize, Ordering};

    let connections = Arc::new(AtomicUsize::new(0));

    // SAFETY: all libc calls use initialized Linux sockaddr_vm values, checked
    // return codes, and OwnedFd closes each accepted descriptor exactly once.
    unsafe {
        let raw = libc::socket(libc::AF_VSOCK, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0);
        if raw < 0 {
            return Err(io::Error::last_os_error());
        }
        let _listener = OwnedFd::from_raw_fd(raw);
        let mut address: libc::sockaddr_vm = zeroed();
        address.svm_family = libc::AF_VSOCK as libc::sa_family_t;
        address.svm_cid = libc::VMADDR_CID_ANY;
        address.svm_port = VSOCK_PORT;
        if libc::bind(
            raw,
            &address as *const _ as *const libc::sockaddr,
            size_of::<libc::sockaddr_vm>() as libc::socklen_t,
        ) != 0
        {
            return Err(io::Error::last_os_error());
        }
        if libc::listen(raw, 16) != 0 {
            return Err(io::Error::last_os_error());
        }
        loop {
            let accepted = libc::accept4(
                raw,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                libc::SOCK_CLOEXEC,
            );
            if accepted < 0 {
                let error = io::Error::last_os_error();
                if error.kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(error);
            }
            let connection = OwnedFd::from_raw_fd(accepted);
            let reader = std::fs::File::from(connection.try_clone()?);
            let writer = std::fs::File::from(connection);
            // One thread per connection, rather than serving each to
            // completion inside the accept loop. The host opens a separate
            // connection for its health probe, and while this loop answered
            // one request at a time a long operation -- a callback wait, an
            // image pull -- left every later connection sitting in the listen
            // backlog until it finished. The probe timed out and the host
            // concluded the guest was gone. Mutating operations are still
            // serialised, inside `handle`.
            if connections.load(Ordering::Acquire) >= MAX_CONCURRENT_CONNECTIONS {
                // Closing is the honest answer: the host retries, and an
                // unbounded thread per connection is a worse failure than a
                // refused one.
                drop(reader);
                drop(writer);
                continue;
            }
            connections.fetch_add(1, Ordering::AcqRel);
            let service = service.clone();
            let owned = Arc::clone(&connections);
            if let Err(error) = thread::Builder::new()
                .name("guestd-connection".into())
                .spawn(move || {
                    let _ = handle_stream(reader, writer, &service);
                    owned.fetch_sub(1, Ordering::AcqRel);
                })
            {
                connections.fetch_sub(1, Ordering::AcqRel);
                return Err(error);
            }
        }
        #[allow(unreachable_code)]
        drop(_listener);
    }
}

#[cfg(not(target_os = "linux"))]
pub fn serve_vsock<E: Engine + 'static>(_service: &GuestService<E>) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "AF_VSOCK guest service is Linux-only",
    ))
}
