//! What the daemon says about itself before it says anything else.

use super::*;

impl Daemon {
    /// The first event on every connection: which daemon this is, and which
    /// build of it.
    ///
    /// Its own function because the shell decides from it whether to adopt
    /// this daemon or replace it, and a decision that important should be
    /// readable -- and drivable by a test -- without a socket.
    pub(super) fn hello_event(&self) -> Value {
        json!({
            "v": PROTOCOL_VERSION,
            "event": "hello",
            "protocol": PROTOCOL_VERSION,
            "daemon_version": DAEMON_VERSION,
            "daemon_api_revision": DAEMON_API_REVISION,
            "pid": std::process::id(),
            // Which binary is actually serving this socket, resolved through
            // the filesystem rather than argv. A replaced app bundle keeps
            // running from wherever its executable went — ~/.Trash, in the
            // case this was written for — and the shell has no other way to
            // tell that the daemon answering it is not the one it ships.
            // Same version, same API revision, different build.
            //
            // Read now rather than at startup, unlike the stamp below: the
            // question this answers is where the running binary *is*, and on
            // macOS that changes under a daemon that keeps serving.
            "executable": std::env::current_exe()
                .and_then(|path| std::fs::canonicalize(&path).or(Ok(path)))
                .ok()
                .map(|path| path.to_string_lossy().into_owned()),
            // And which build is at that path. On Windows an in-place update
            // writes to the *same* path, so the path alone says nothing: a
            // daemon from the previous version answers with an identical one
            // and gets adopted by the new shell, which then supervises the old
            // runtime with the same version reported on both sides. Size and
            // mtime, because an installer that writes a new file changes both
            // and reading the whole binary on every handshake to learn the
            // same thing is not worth it.
            "executable_size": self.executable_stamp.map(|(size, _)| size),
            "executable_modified_ms": self.executable_stamp
                .map(|(_, modified)| modified.to_string()),
            "compatibility_supervisor": self.managed_runtime.is_none(),
            "mode": if self.managed_runtime.is_some() {
                "managed-local"
            } else if self.host_processes.is_some() {
                "host-packs"
            } else {
                "compatibility"
            },
            "host_pack_release": self.host_processes.as_ref().map(|manager| manager.release()),
            "host_pack_root": self.host_pack_root.as_deref(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::daemon;
    use serde_json::json;

    /// The handshake describes the binary this daemon started from, not
    /// whichever file is at its path when a client happens to connect.
    ///
    /// Measured at handshake time it described neither. An in-place Windows
    /// update replaces the executable under a daemon that is still running, so
    /// the old daemon read the *new* file and answered with the identity of
    /// the build that had just replaced it -- and the shell, finding the
    /// path and the stamp both matching what it ships, adopted the one daemon
    /// the stamp exists to refuse. Read once, before `serve` binds the socket,
    /// it can only ever describe itself.
    #[test]
    fn the_handshake_describes_the_build_this_daemon_started_from() {
        let (_root, mut daemon) = daemon();
        // What a daemon whose executable was replaced underneath it still has
        // to say. Nothing else in the process can produce these numbers, so a
        // handshake that measures the file instead cannot report them.
        let started_from = (4_242_u64, 1_234_567_u128);
        std::sync::Arc::get_mut(&mut daemon)
            .expect("the test holds the only reference")
            .executable_stamp = Some(started_from);

        let hello = daemon.hello_event();
        assert_eq!(hello["executable_size"], json!(4_242));
        assert_eq!(hello["executable_modified_ms"], json!("1234567"));
    }

    /// A daemon that cannot measure its own binary says so, rather than
    /// leaving the field out and being taken for an older build that never
    /// had it.
    #[test]
    fn a_daemon_that_cannot_measure_itself_reports_nothing_rather_than_guessing() {
        let (_root, mut daemon) = daemon();
        std::sync::Arc::get_mut(&mut daemon)
            .expect("the test holds the only reference")
            .executable_stamp = None;
        let hello = daemon.hello_event();
        assert!(hello["executable_size"].is_null());
        assert!(hello["executable_modified_ms"].is_null());
    }
}
