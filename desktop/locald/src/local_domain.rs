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
//! [`Resolution::Resolver`] is a domain we own, resolved by a `/etc/resolver`
//! file pointing at locald. It works offline, which the public route does not,
//! and it does not put this installation's hostnames in front of a third party's
//! nameservers. It also costs an admin prompt -- which would be the first
//! privileged thing Lemma ever does -- so it is not the default.
//!
//! Swapping one for the other is a value change here and nothing else.

use std::env;

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
    /// Read the configured domain, falling back to `*.localhost`.
    #[must_use]
    pub fn from_env() -> Self {
        Self::parse(env::var(OVERRIDE).ok().as_deref())
    }

    /// The parsing half, so it can be tested without touching the environment.
    #[must_use]
    pub fn parse(configured: Option<&str>) -> Self {
        let configured = configured.map(str::trim).unwrap_or_default();
        if configured.is_empty() || configured.eq_ignore_ascii_case(LOCALHOST_BASE) {
            return Self::default();
        }
        if configured.eq_ignore_ascii_case("sslip") {
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
    fn the_default_is_the_localhost_convention() {
        let domain = LocalDomain::parse(None);
        assert_eq!(domain.base(), "lemma.localhost");
        assert_eq!(domain.resolution(), Resolution::Convention);
        // ...and an app framed by the workspace cannot hold a session there,
        // which is the whole reason the other modes exist.
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
    fn a_localhost_domain_is_not_a_configured_one() {
        // Accepting it would read as configured while behaving like the
        // default: same-site would still be false, apps would still be
        // window-only, and the setting would look like it had taken effect.
        assert_eq!(
            LocalDomain::parse(Some("other.localhost")),
            LocalDomain::default()
        );
        assert_eq!(LocalDomain::parse(Some("  ")), LocalDomain::default());
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
