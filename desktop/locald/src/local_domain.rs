//! The domain this installation serves itself under.
//!
//! One place, because the hostname had grown four independent copies -- two
//! inlined in `network.rs`, one constant in `native_host_pack.rs`, one more in
//! `daemon.rs` -- and a fifth in `lemma-stack`. A rule spelled five times is a
//! rule that only holds until someone edits four of them.
//!
//! # Why it is configurable at all
//!
//! `localhost` is not in the Public Suffix List, so WebKit cannot derive a
//! registrable domain from `app.lemma.localhost` and treats every
//! `*.lemma.localhost` host as its own *site*. A pod app framed by the
//! workspace is therefore third-party, and WebKit gives a third-party frame no
//! storage at all -- no cookie stored, no `document.cookie`, no storage access.
//! The app renders permanently signed out and no cookie attribute can change
//! it, because third-party is decided against the top frame.
//!
//! A real registrable domain that answers 127.0.0.1 fixes it outright: the
//! workspace and the app become same-site, exactly as they already are on
//! `lemma.work`.
//!
//! # The two ways to get one
//!
//! [`Resolution::Public`] is a domain whose wildcard is answered by public DNS.
//! `sslip.io` is one such service and needs nothing installed, bought or run --
//! `<anything>.127.0.0.1.sslip.io` answers `127.0.0.1`, and `sslip.io` is not
//! itself a public suffix, so it is the registrable domain and our two hosts
//! are same-site under it.
//!
//! What it costs, stated plainly because it is the shipped default: the first
//! lookup of each hostname goes to a third party's nameservers, and an app
//! hostname contains that app's slug. Nothing else leaves the machine -- the
//! answer is `127.0.0.1` and every request is loopback -- but the names are
//! seen. This is the reason [`Resolution::Resolver`] exists and is where this
//! is meant to end up; it is waiting on a domain to own rather than on code.
//!
//! [`Resolution::Resolver`] is a domain we own, resolved by a `/etc/resolver`
//! file pointing at locald. It works offline, which the public route does not,
//! and it does not put this installation's hostnames in front of a third party's
//! nameservers. It also costs an admin prompt -- which would be the first
//! privileged thing Lemma ever does -- and, more to the point, a domain we
//! actually own: pointing a resolver file at a name somebody else holds
//! shadows the real one for anybody running this. So it is not the default
//! yet. Everything here already accepts such a domain, so arriving at one is a
//! value change and a responder, not a redesign.
//!
//! Swapping one for the other is a value change here and nothing else.

use std::env;
use std::net::{IpAddr, Ipv4Addr, ToSocketAddrs};
use std::sync::OnceLock;

/// The shipped default: no DNS, no privilege, and pod apps stay window-only on
/// macOS because an embedded one cannot hold a session.
pub const LOCALHOST_BASE: &str = "lemma.localhost";

/// A public wildcard that answers loopback. See the module docs.
pub const SSLIP_BASE: &str = "127.0.0.1.sslip.io";

/// `sslip` for the public wildcard, or a domain of our own to resolve locally.
const OVERRIDE: &str = "LEMMA_LOCAL_DOMAIN";

/// How a base domain becomes an address.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Resolution {
    /// `*.localhost`, which every resolver answers by convention.
    Convention,
    /// Public DNS answers the wildcard. Needs the network, needs nothing else.
    Public,
    /// A domain we own, answered by a resolver file pointing at locald.
    Resolver,
}

/// The base domain, and how it resolves.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalDomain {
    base: String,
    resolution: Resolution,
}

impl Default for LocalDomain {
    fn default() -> Self {
        Self {
            base: LOCALHOST_BASE.to_owned(),
            resolution: Resolution::Convention,
        }
    }
}

impl LocalDomain {
    /// The domain to serve under, checked against what actually resolves.
    ///
    /// Defaults to the public wildcard, because that is the only mode in which
    /// a pod app embedded in the workspace can hold a session -- see the module
    /// docs. But it is DNS, and a laptop with no network would otherwise fail
    /// to resolve its own workspace and open on nothing at all, which is a poor
    /// trade for a product whose whole point is running locally.
    ///
    /// So the name is probed, once, and a failure falls back to `*.localhost`:
    /// the workspace works offline exactly as it does today, and apps open in
    /// their own window instead of embedded. Degrading rather than refusing.
    /// Resolved once per process, because every caller has to agree.
    ///
    /// The rendered host pack, the URLs locald announces, and the state it
    /// migrates all have to name the same host -- and the probe is a DNS
    /// lookup, which `network.rs` would otherwise perform on every call that
    /// builds a URL.
    #[must_use]
    pub fn from_env() -> Self {
        static RESOLVED: OnceLock<LocalDomain> = OnceLock::new();
        RESOLVED
            .get_or_init(|| {
                let configured = Self::parse(env::var(OVERRIDE).ok().as_deref());
                if configured.resolution == Resolution::Public && !configured.resolves() {
                    return Self::default();
                }
                configured
            })
            .clone()
    }

    /// Whether this domain's workspace host answers loopback right now.
    ///
    /// Loopback specifically, not merely "resolves": a wildcard service that
    /// started answering something else, or a captive portal resolving every
    /// name to its own address, would otherwise send the workspace at somebody
    /// else's machine.
    fn resolves(&self) -> bool {
        let probe = format!("{}:80", self.frontend_host());
        probe.to_socket_addrs().is_ok_and(|mut found| {
            found.any(|address| address.ip() == IpAddr::V4(Ipv4Addr::LOCALHOST))
        })
    }

    /// The parsing half, so it can be tested without touching the environment.
    #[must_use]
    pub fn parse(configured: Option<&str>) -> Self {
        let configured = configured.map(str::trim).unwrap_or_default();
        if configured.eq_ignore_ascii_case(LOCALHOST_BASE)
            || configured.eq_ignore_ascii_case("localhost")
        {
            return Self::default();
        }
        if configured.is_empty() || configured.eq_ignore_ascii_case("sslip") {
            return Self {
                base: SSLIP_BASE.to_owned(),
                resolution: Resolution::Public,
            };
        }
        // A domain of our own. `.localhost` is rejected here rather than
        // silently accepted: it would read as "configured" while behaving like
        // the default, and the difference decides whether apps can be embedded.
        let base = configured.to_ascii_lowercase();
        if base.ends_with(".localhost") {
            return Self::default();
        }
        Self {
            base,
            resolution: Resolution::Resolver,
        }
    }

    #[must_use]
    pub fn base(&self) -> &str {
        &self.base
    }

    #[must_use]
    pub fn resolution(&self) -> Resolution {
        self.resolution
    }

    /// Whether an app framed by the workspace can hold a session here.
    ///
    /// True exactly when a browser can derive a registrable domain covering
    /// both hosts, which is what makes them same-site.
    #[must_use]
    pub fn frames_carry_cookies(&self) -> bool {
        self.resolution != Resolution::Convention
    }

    /// The workspace and API host. One host, two ports, deliberately.
    #[must_use]
    pub fn frontend_host(&self) -> String {
        format!("app.{}", self.base)
    }

    /// Where apps are served: `<slug>.apps.<base>`.
    #[must_use]
    pub fn apps_domain(&self) -> String {
        format!("apps.{}", self.base)
    }

    /// The session cookie's scope, wide enough to reach the app subdomains.
    #[must_use]
    pub fn cookie_domain(&self) -> String {
        format!(".{}", self.base)
    }

    /// Every origin this installation serves, for CORS.
    #[must_use]
    pub fn cors_origin_regex(&self) -> String {
        format!(
            r"^https?://([a-z0-9-]+\.)*{}(:\d+)?$",
            self.base.replace('.', r"\.")
        )
    }

    /// Whether `host` belongs to this installation.
    ///
    /// The navigation gate needs this: a public name that answers 127.0.0.1 is
    /// a local destination whatever DNS says about it.
    #[must_use]
    pub fn owns_host(&self, host: &str) -> bool {
        let host = host
            .split(':')
            .next()
            .unwrap_or_default()
            .to_ascii_lowercase();
        host == self.base || host.ends_with(&format!(".{}", self.base))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_is_a_domain_a_framed_app_can_hold_a_session_on() {
        // Measured, not assumed. In a WKWebView an iframe on a sibling host
        // under a *made-up* TLD gets no storage at all -- `document.cookie`
        // reads empty and the server's Set-Cookie is not kept -- exactly as it
        // does under `.localhost`. Under a real registrable domain both work.
        // So a domain we could invent is not an option; the default has to be
        // one a browser can derive a registrable domain from.
        let domain = LocalDomain::parse(None);
        assert_eq!(domain.base(), SSLIP_BASE);
        assert_eq!(domain.resolution(), Resolution::Public);
        assert!(domain.frames_carry_cookies());
    }

    #[test]
    fn the_localhost_convention_is_still_reachable_on_purpose() {
        // The offline fallback lands here, and so does anyone who wants the
        // old arrangement. Apps then open in their own window rather than
        // embedded, which works.
        let domain = LocalDomain::parse(Some(LOCALHOST_BASE));
        assert_eq!(domain.resolution(), Resolution::Convention);
        assert!(!domain.frames_carry_cookies());
    }

    #[test]
    fn sslip_is_a_registrable_domain_so_frames_carry_cookies() {
        let domain = LocalDomain::parse(Some("sslip"));
        assert_eq!(domain.frontend_host(), "app.127.0.0.1.sslip.io");
        assert_eq!(domain.apps_domain(), "apps.127.0.0.1.sslip.io");
        assert_eq!(domain.cookie_domain(), ".127.0.0.1.sslip.io");
        assert!(domain.frames_carry_cookies());
    }

    #[test]
    fn a_domain_of_our_own_is_taken_literally() {
        let domain = LocalDomain::parse(Some("Lemma-Local.example"));
        assert_eq!(domain.base(), "lemma-local.example");
        assert_eq!(domain.resolution(), Resolution::Resolver);
        assert!(domain.frames_carry_cookies());
    }

    #[test]
    fn a_localhost_domain_never_reads_as_a_configured_one() {
        // Accepting one would look like the setting had taken effect while
        // behaving exactly like not setting it: same-site still false, apps
        // still window-only.
        assert_eq!(
            LocalDomain::parse(Some("other.localhost")).resolution(),
            Resolution::Convention
        );
        // Blank means "unset", which is the default -- not the fallback.
        assert_eq!(
            LocalDomain::parse(Some("  ")).resolution(),
            Resolution::Public
        );
    }

    #[test]
    fn the_cors_regex_covers_every_host_and_escapes_the_dots() {
        let regex = LocalDomain::parse(Some("sslip")).cors_origin_regex();
        assert!(regex.contains(r"127\.0\.0\.1\.sslip\.io"));
        // Unescaped, `.` matches anything -- `127x0y0z1.sslip.io` would pass.
        assert!(!regex.contains("127.0.0.1.sslip.io"));
    }

    #[test]
    fn a_host_under_the_base_belongs_to_this_installation() {
        let domain = LocalDomain::parse(Some("sslip"));
        assert!(domain.owns_host("app.127.0.0.1.sslip.io"));
        assert!(domain.owns_host("my-app.apps.127.0.0.1.sslip.io:8711"));
        assert!(domain.owns_host("127.0.0.1.sslip.io"));
        // A different sslip address is somebody else's loopback, not ours.
        assert!(!domain.owns_host("app.10.0.0.1.sslip.io"));
        assert!(!domain.owns_host("evil.example"));
    }
}
