pub mod agent_host;
pub mod config_operations;
pub mod daemon;
pub mod host_process;
mod lifecycle;
pub mod local_domain;
pub mod managed_runtime;
pub mod native_host_pack;
pub mod network;
pub mod operator_config;
pub mod paths;
pub mod port_reservation;
pub mod protocol;
pub mod provider_probe;
pub mod reset;
pub mod sharing;
pub mod state;
mod tcp_forwarder;
pub mod telemetry;
pub mod update_transaction;
pub mod vault_process;

pub const PROTOCOL_VERSION: u64 = 1;

/// Spawn a child without flashing up a console window.
///
/// locald is started by a GUI app that has no console of its own, and nearly
/// everything it spawns -- the backend, the frontend, python.exe, node.exe,
/// cloudflared, taskkill -- is a console-subsystem program. Creating one of
/// those from a process with no console makes Windows allocate a fresh conhost
/// window for it, which the user sees next to the app and can close, taking
/// the child with it. Redirecting stdio does not suppress that window; only
/// CREATE_NO_WINDOW does.
///
/// A no-op everywhere else, so call sites stay platform-neutral. Note that
/// `creation_flags` replaces the flag set rather than adding to it, so a site
/// that needs more than this one spells all of them out itself.
pub(crate) trait NoConsoleWindow {
    fn no_console_window(&mut self) -> &mut Self;
}

impl NoConsoleWindow for std::process::Command {
    #[cfg(windows)]
    fn no_console_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(CREATE_NO_WINDOW)
    }

    #[cfg(not(windows))]
    fn no_console_window(&mut self) -> &mut Self {
        self
    }
}

/// Windows process creation flags used across the crate.
#[cfg(windows)]
pub(crate) const CREATE_NO_WINDOW: u32 = 0x0800_0000;
#[cfg(windows)]
pub(crate) const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;

/// Join a test's server thread, or fail rather than hang the binary.
///
/// These tests spawn a thread that blocks in `accept()` and then reads a fixed
/// number of bytes. If the client under test connects zero times, or one time
/// too few, or sends a byte less than expected, that thread never returns -- and
/// an unconditional `join()` waits with it, forever, taking every remaining
/// test in the binary with it. `host_process.rs` carries the note about how
/// that "burned 44 minutes of a CI runner before it was cancelled rather than
/// failing".
///
/// The blocked thread is left where it is: a thread parked in a syscall cannot
/// be cancelled in Rust, and it dies with the process at the end of the run.
/// What changes is that the run reaches the end.
///
/// Here rather than in each module because there are five of these across four
/// files, and the first fix copied it into one of them.
#[cfg(test)]
pub(crate) fn join_within<T>(handle: std::thread::JoinHandle<T>, what: &str) -> T {
    join_before(handle, what, std::time::Duration::from_secs(20))
}

/// The same, with the deadline named.
///
/// Only the helper's own test passes one: it needs to prove the deadline fires,
/// and paying the real twenty seconds to do that would put this file's tests
/// among the slowest in the crate for no extra confidence.
#[cfg(test)]
pub(crate) fn join_before<T>(
    handle: std::thread::JoinHandle<T>,
    what: &str,
    timeout: std::time::Duration,
) -> T {
    let deadline = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < deadline {
        if handle.is_finished() {
            return handle.join().expect("the server thread panicked");
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    panic!("{what} never finished; it is still blocked on the socket");
}

/// Every file the daemon was split into.
///
/// `daemon.rs` was one 3,261-line file, and three guards below scanned it
/// by name. It is a directory now. A guard still naming the old file would
/// have failed to compile, which is the good case; one updated to name a
/// single new file would have gone on passing while checking a fraction of
/// what it used to. So the list lives once, and the test under it fails if
/// a module is added to the directory without being added here.
#[cfg(test)]
const DAEMON_SOURCES: &[(&str, &str)] = &[
    (
        "daemon/agent_host_ops.rs",
        include_str!("daemon/agent_host_ops.rs"),
    ),
    ("daemon/config_ops.rs", include_str!("daemon/config_ops.rs")),
    ("daemon/dispatch.rs", include_str!("daemon/dispatch.rs")),
    (
        "daemon/environment.rs",
        include_str!("daemon/environment.rs"),
    ),
    ("daemon/mod.rs", include_str!("daemon/mod.rs")),
    ("daemon/monitors.rs", include_str!("daemon/monitors.rs")),
    ("daemon/reset_ops.rs", include_str!("daemon/reset_ops.rs")),
    (
        "daemon/sharing_ops.rs",
        include_str!("daemon/sharing_ops.rs"),
    ),
    ("daemon/stack_ops.rs", include_str!("daemon/stack_ops.rs")),
    ("daemon/supervisor.rs", include_str!("daemon/supervisor.rs")),
    ("daemon/tests.rs", include_str!("daemon/tests.rs")),
];

/// Every `.rs` file under a directory, at any depth, named relative to it.
///
/// Recursive on purpose. `read_dir` sees direct children only, so the moment
/// one of these modules becomes a directory of its own -- which is how every
/// file in this crate over 600 lines will end up -- its contents leave the
/// scan without anything saying so.
#[cfg(test)]
fn rust_files_under(directory: &std::path::Path, prefix: &str) -> Vec<String> {
    let mut found = Vec::new();
    let mut pending = vec![(directory.to_path_buf(), prefix.to_owned())];
    while let Some((next, at)) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&next) else {
            continue;
        };
        for entry in entries.filter_map(Result::ok) {
            let name = entry.file_name().to_string_lossy().into_owned();
            let path = entry.path();
            if path.is_dir() {
                pending.push((path, format!("{at}{name}/")));
            } else if name.ends_with(".rs") {
                found.push(format!("{at}{name}"));
            }
        }
    }
    found.sort();
    found
}

/// A module in a subdirectory is one the scan would otherwise not see.
#[cfg(test)]
#[test]
fn a_daemon_module_one_directory_deeper_is_still_found() {
    let root = tempfile::tempdir().expect("a temporary directory");
    std::fs::write(root.path().join("top.rs"), "").expect("a top-level module");
    std::fs::create_dir(root.path().join("nested")).expect("a nested directory");
    std::fs::write(root.path().join("nested/inner.rs"), "").expect("a nested module");
    std::fs::write(root.path().join("nested/notes.txt"), "").expect("a non-module file");
    assert_eq!(
        rust_files_under(root.path(), "daemon/"),
        vec![
            "daemon/nested/inner.rs".to_string(),
            "daemon/top.rs".to_string()
        ],
    );
}

/// A daemon module nobody scans is a rule nobody enforces.
#[cfg(test)]
#[test]
fn every_daemon_source_is_scanned() {
    let directory = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/daemon");
    let on_disk = rust_files_under(&directory, "daemon/");
    let mut listed: Vec<String> = DAEMON_SOURCES
        .iter()
        .map(|(name, _)| (*name).to_owned())
        .collect();
    listed.sort();
    assert_eq!(
        on_disk, listed,
        "every file under src/daemon has to be in DAEMON_SOURCES, or the \
         guards that scan the daemon silently stop covering it"
    );
}

#[cfg(test)]
mod doc_comment_policy {
    use super::DAEMON_SOURCES;

    /// An indented block in a doc comment is a *Rust* code block.
    ///
    /// rustdoc treats four spaces after `///` as code and tries to compile it,
    /// so quoting a log line that way turns prose into a doctest that cannot
    /// parse. One did: quoting PostgreSQL's refusal failed three CI jobs with
    /// "expected one of `!` or `::`, found `around`", and not one of those
    /// three names a doc comment.
    ///
    /// The fix is always a fenced text block. This finds the shape here,
    /// where it costs seconds, rather than on a push.
    #[test]
    fn no_doc_comment_quotes_prose_as_an_indented_code_block() {
        let sources: Vec<(&str, &str)> = [
            ("locald/src/lib.rs", include_str!("lib.rs")),
            (
                "locald/src/host_process.rs",
                include_str!("host_process.rs"),
            ),
            (
                "local-runtime/guestd/src/lib.rs",
                include_str!("../../local-runtime/guestd/src/lib.rs"),
            ),
        ]
        .into_iter()
        .chain(DAEMON_SOURCES.iter().copied())
        .collect();

        let mut offenders = Vec::new();
        for (name, source) in sources {
            let mut fenced = false;
            for (number, line) in source.replace("\r\n", "\n").lines().enumerate() {
                let Some(doc) = line.trim_start().strip_prefix("///") else {
                    continue;
                };
                if doc.trim_start().starts_with("```") {
                    fenced = !fenced;
                    continue;
                }
                if fenced || doc.trim().is_empty() {
                    continue;
                }
                if doc.starts_with("    ") {
                    offenders.push(format!("{name}:{}: {}", number + 1, line.trim()));
                }
            }
        }

        assert!(
            offenders.is_empty(),
            "these are compiled as doctests; fence them as a text block instead:\n{}",
            offenders.join("\n"),
        );
    }
}

#[cfg(test)]
mod join_within_policy {
    /// The helper fails instead of hanging, and does not slow a healthy join.
    ///
    /// The behaviour is the whole point: five tests across four files hand it a
    /// thread that is blocked in `accept()`, and the failure mode it replaces is
    /// a test binary that never exits.
    #[test]
    fn a_thread_that_never_finishes_fails_rather_than_hanging() {
        let started = std::time::Instant::now();
        // Never signalled, so the thread parks for the life of the process --
        // exactly like an `accept()` nobody connects to.
        let (_keep, receiver) = std::sync::mpsc::channel::<()>();
        let stuck = std::thread::spawn(move || receiver.recv());

        let panicked = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            super::join_before(
                stuck,
                "a thread that never finishes",
                std::time::Duration::from_millis(200),
            )
        }));

        assert!(panicked.is_err(), "it must fail, not return");
        // Bounded by its deadline rather than by the life of the run, which is
        // the whole property. Generous ceiling so a loaded machine does not
        // turn this into the flake it exists to prevent.
        assert!(started.elapsed() < std::time::Duration::from_secs(5));
    }

    #[test]
    fn a_thread_that_finishes_is_joined_immediately() {
        let started = std::time::Instant::now();
        let quick = std::thread::spawn(|| 7);
        assert_eq!(super::join_within(quick, "a thread that finishes"), 7);
        assert!(
            started.elapsed() < std::time::Duration::from_secs(2),
            "a healthy join must not pay the polling interval",
        );
    }
}

#[cfg(test)]
mod http_client_policy {
    use super::DAEMON_SOURCES;

    /// Every HTTP client locald builds must opt out of the system proxy.
    ///
    /// locald only ever talks to the stack it is itself supervising: the
    /// backend and frontend it launched, the managed runtime it booted, the
    /// ngrok agent API on loopback. A proxy configured without a `<local>`
    /// bypass would route all of that at something that has never heard of it,
    /// and the symptom — "Lemma won't start" on one machine, on one network —
    /// is close to undiagnosable from a bug report.
    ///
    /// This used to hold for free: locald's own manifest asked reqwest for
    /// `blocking, json, native-tls` and nothing else, so there was no
    /// system-proxy support compiled in to accidentally use. Sharing one
    /// dependency graph with the desktop shell and the agent host — which do
    /// want `system-proxy`, for real outbound calls — means feature
    /// unification hands it to locald too. Nothing in the type system objects.
    /// So this does.
    #[test]
    fn every_client_locald_builds_opts_out_of_the_system_proxy() {
        let sources: Vec<(&str, &str)> = [
            ("sharing.rs", include_str!("sharing.rs")),
            ("provider_probe.rs", include_str!("provider_probe.rs")),
            ("agent_host.rs", include_str!("agent_host.rs")),
            ("host_process.rs", include_str!("host_process.rs")),
            ("native_host_pack.rs", include_str!("native_host_pack.rs")),
            ("network.rs", include_str!("network.rs")),
            ("operator_config.rs", include_str!("operator_config.rs")),
            ("managed_runtime.rs", include_str!("managed_runtime.rs")),
        ]
        .into_iter()
        .chain(DAEMON_SOURCES.iter().copied())
        .collect();

        for (name, source) in sources {
            for (offset, _) in source.match_indices("Client::builder()") {
                // The builder chain runs until the `.build()` that ends it.
                let rest = &source[offset..];
                let chain = rest.find(".build()").map_or(rest, |end| &rest[..end]);
                assert!(
                    chain.contains(".no_proxy()"),
                    "{name}: a reqwest client is built without .no_proxy(). locald \
                     talks to the stack it supervises; a system proxy must not be \
                     consulted for that. Chain was:\n{chain}"
                );
            }
        }
    }
}

#[cfg(test)]
mod console_window_policy {
    use super::DAEMON_SOURCES;

    /// Nothing locald spawns may open a console window.
    ///
    /// locald is started by a GUI app that has no console of its own, and every
    /// child here -- the backend, the frontend, python.exe, node.exe,
    /// cloudflared, taskkill, uv -- is a console-subsystem program. Windows
    /// gives such a child a brand new conhost window when its parent has none.
    /// The user sees it sitting next to the app, and closing it kills the
    /// child. Redirecting stdio does not suppress it.
    ///
    /// Commands named by an absolute POSIX path are exempt: they cannot run on
    /// Windows at all.
    #[test]
    fn every_spawned_command_suppresses_its_console_window() {
        let sources: Vec<(&str, &str)> = [
            ("agent_host.rs", include_str!("agent_host.rs")),
            ("host_process.rs", include_str!("host_process.rs")),
            ("managed_runtime.rs", include_str!("managed_runtime.rs")),
            ("sharing.rs", include_str!("sharing.rs")),
        ]
        .into_iter()
        .chain(DAEMON_SOURCES.iter().copied())
        .collect();

        for (name, source) in sources {
            for (offset, _) in source.match_indices("Command::new(") {
                let rest = &source[offset..];
                if rest["Command::new(".len()..].starts_with("\"/") {
                    continue;
                }
                // The chain runs until whatever actually starts the process.
                let chain = ["spawn()", "output()", "status()"]
                    .iter()
                    .filter_map(|terminator| rest.find(terminator))
                    .min()
                    .map_or(rest, |end| &rest[..end]);
                assert!(
                    chain.contains("no_console_window") || chain.contains("creation_flags"),
                    "{name}: a command is spawned without suppressing its console \
                     window. Chain was:\n{chain}"
                );
            }
        }
    }
}
mod credential_vault;
