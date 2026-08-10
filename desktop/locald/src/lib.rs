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
