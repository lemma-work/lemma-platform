//! The domain this installation serves itself under: `lemma.localhost`.
//!
//! One place, because the hostname had grown four independent copies -- two
//! inlined in `network.rs`, one constant in `native_host_pack.rs`, one more in
//! `daemon.rs` -- and a fifth in `lemma-stack`. A rule spelled five times is a
//! rule that only holds until someone edits four of them.
//!
//! # Why `*.localhost`, and what it costs
//!
//! Every resolver answers `*.localhost` with loopback by convention, and
//! Chromium, WebView2 and WebKit all resolve it themselves without asking DNS.
//! So the workspace works offline, puts no hostname in front of anybody's
//! nameservers, and needs nothing installed. Every browser treats it as a
//! secure context too, which is what gives the workspace the microphone, the
//! async clipboard and `crypto.subtle` over plain `http`.
//!
//! The cost is on macOS alone. WebKit asks CFNetwork whether a name is a
//! top-level domain to derive a site from it, and `localhost` is not one -- so
//! every `*.localhost` *host* is its own site. A pod app on
//! `<slug>.apps.lemma.localhost` framed by `app.lemma.localhost` is then
//! third-party and WebKit gives it no cookies. Opened top level it is signed in
//! (a `Domain=lemma.localhost` cookie is stored and sent), and framing the
//! *same host on another port* is same-site. The macOS shell therefore frames
//! apps through an alias on the workspace host -- see `crate::app_alias`.
//!
//! This replaced a public loopback wildcard (`127.0.0.1.sslip.io`), which made
//! the frames same-site at the price of a DNS dependency (no network, no
//! workspace), a third party seeing every app hostname, and losing the secure
//! context. Nothing here resolves through public DNS any more.

/// The base every host this installation serves sits under.
pub const LOCALHOST_BASE: &str = "lemma.localhost";

/// The base domain, and the hosts derived from it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalDomain {
    base: String,
}

impl Default for LocalDomain {
    fn default() -> Self {
        Self {
            base: LOCALHOST_BASE.to_owned(),
        }
    }
}

impl LocalDomain {
    /// The domain this installation serves under.
    ///
    /// Fixed. It used to be probed and could fall back, which made every URL
    /// locald announced depend on whether the network was up at launch.
    #[must_use]
    pub fn current() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn base(&self) -> &str {
        &self.base
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
    #[must_use]
    pub fn owns_host(&self, host: &str) -> bool {
        let host = host
            .split(':')
            .next()
            .unwrap_or_default()
            .to_ascii_lowercase();
        host == self.base || host.ends_with(&format!(".{}", self.base))
    }

    /// The app label in `host` (`orders` in `orders.apps.lemma.localhost`), if
    /// `host` is one of this installation's app hosts.
    ///
    /// One label only, as the backend's host routing accepts: a multi-level
    /// name under the apps domain routes nowhere, so it is not aliased either.
    #[must_use]
    pub fn app_label<'a>(&self, host: &'a str) -> Option<&'a str> {
        let suffix = format!(".{}", self.apps_domain());
        let label = host.strip_suffix(&suffix)?;
        let valid = !label.is_empty()
            && label.len() <= 63
            && !label.starts_with('-')
            && label
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-');
        valid.then_some(label)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_domain_is_lemma_localhost_and_needs_no_dns() {
        let domain = LocalDomain::current();
        assert_eq!(domain.base(), "lemma.localhost");
        assert_eq!(domain.frontend_host(), "app.lemma.localhost");
        assert_eq!(domain.apps_domain(), "apps.lemma.localhost");
        assert_eq!(domain.cookie_domain(), ".lemma.localhost");
    }

    #[test]
    fn the_cors_regex_covers_every_host_and_escapes_the_dots() {
        let regex = LocalDomain::current().cors_origin_regex();
        assert!(regex.contains(r"lemma\.localhost"));
        // Unescaped, `.` matches anything -- `lemmaxlocalhost` would pass.
        assert!(!regex.contains("lemma.localhost"));
    }

    #[test]
    fn a_host_under_the_base_belongs_to_this_installation() {
        let domain = LocalDomain::current();
        assert!(domain.owns_host("app.lemma.localhost"));
        assert!(domain.owns_host("my-app.apps.lemma.localhost:8711"));
        assert!(domain.owns_host("lemma.localhost"));
        assert!(!domain.owns_host("app.lemma.localhost.evil"));
        assert!(!domain.owns_host("other.localhost"));
        assert!(!domain.owns_host("evil.example"));
    }

    #[test]
    fn only_a_single_label_app_host_is_an_app() {
        let domain = LocalDomain::current();
        assert_eq!(
            domain.app_label("orders.apps.lemma.localhost"),
            Some("orders")
        );
        assert_eq!(
            domain.app_label("orders--r7.apps.lemma.localhost"),
            Some("orders--r7")
        );
        assert_eq!(domain.app_label("apps.lemma.localhost"), None);
        assert_eq!(domain.app_label("a.b.apps.lemma.localhost"), None);
        assert_eq!(domain.app_label("app.lemma.localhost"), None);
        assert_eq!(domain.app_label("Orders.apps.lemma.localhost"), None);
        assert_eq!(domain.app_label("orders.apps.lemma.localhost.evil"), None);
    }
}
