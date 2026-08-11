pub mod agent_host;
pub mod daemon;
pub mod host_process;
pub mod managed_runtime;
pub mod native_host_pack;
pub mod network;
pub mod operator_config;
pub mod paths;
pub mod port_reservation;
pub mod protocol;
pub mod provider_probe;
pub mod sharing;
pub mod state;

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

#[cfg(test)]
mod http_client_policy {
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
        const SOURCES: &[(&str, &str)] = &[
            ("daemon.rs", include_str!("daemon.rs")),
            ("sharing.rs", include_str!("sharing.rs")),
            ("provider_probe.rs", include_str!("provider_probe.rs")),
            ("agent_host.rs", include_str!("agent_host.rs")),
            ("host_process.rs", include_str!("host_process.rs")),
            ("native_host_pack.rs", include_str!("native_host_pack.rs")),
            ("network.rs", include_str!("network.rs")),
            ("operator_config.rs", include_str!("operator_config.rs")),
            ("managed_runtime.rs", include_str!("managed_runtime.rs")),
        ];

        for (name, source) in SOURCES {
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
        const SOURCES: &[(&str, &str)] = &[
            ("agent_host.rs", include_str!("agent_host.rs")),
            ("daemon.rs", include_str!("daemon.rs")),
            ("host_process.rs", include_str!("host_process.rs")),
            ("managed_runtime.rs", include_str!("managed_runtime.rs")),
            ("sharing.rs", include_str!("sharing.rs")),
        ];

        for (name, source) in SOURCES {
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
