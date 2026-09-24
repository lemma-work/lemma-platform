//! Which workspaces a host may be pointed at.

use url::Url;

/// Whether a host name can only ever mean this machine.
///
/// `.localhost` is reserved for exactly that by RFC 6761 — resolvers must not
/// send it to DNS and must answer loopback — so `app.lemma.localhost`, which is
/// the hostname Lemma Desktop serves its own workspace and API on, is as
/// loopback as `127.0.0.1`. Accepting only the three literal spellings meant a
/// desktop install could not pair with itself: locald handed the host its own
/// API URL and the host refused it as a non-loopback plain-HTTP target.
#[must_use]
pub fn is_loopback_host(host: Option<&str>) -> bool {
    let Some(host) = host else {
        return false;
    };
    // The spellings that are loopback by definition, answered without asking
    // the resolver: `localhost` and `.localhost` are reserved to loopback by
    // RFC 6761, and the literals are self-evident.
    if matches!(host, "localhost" | "127.0.0.1" | "::1" | "[::1]") || host.ends_with(".localhost") {
        return true;
    }
    // Otherwise ask what the name actually is.
    //
    // A hardcoded list of spellings is what broke this. Lemma Desktop stopped
    // serving itself on `*.localhost` -- a browser derives no registrable
    // domain from it, so a pod app framed by the workspace can hold no session
    // -- and now serves `app.127.0.0.1.sslip.io:<port>` instead. That is
    // loopback in every way that matters: the name resolves to 127.0.0.1 and
    // the backend binds there. It matched none of the spellings above, so the
    // host refused to pair with the workspace that asked it to, and onboarding
    // sat on "Connecting this computer" with no error anywhere.
    //
    // Resolving answers the question the list was approximating, and keeps
    // answering it when the domain moves again. It is deliberately strict:
    // every address the name resolves to must be loopback, so a name that
    // answers both 127.0.0.1 and a routable address is refused rather than
    // accepted on the strength of its first answer.
    resolves_only_to_loopback(host)
}

/// Whether every address `host` resolves to is loopback, and there is at least
/// one. Used only to decide whether plain HTTP is acceptable to this target.
fn resolves_only_to_loopback(host: &str) -> bool {
    use std::net::ToSocketAddrs;

    // Port 0 -- this is a name lookup, not a connection.
    let Ok(addresses) = (host, 0u16).to_socket_addrs() else {
        return false;
    };
    let mut resolved = false;
    for address in addresses {
        resolved = true;
        if !address.ip().is_loopback() {
            return false;
        }
    }
    resolved
}

pub fn validate_target_url(url: &Url, allow_insecure_http: bool) -> anyhow::Result<()> {
    if url.scheme() == "https" {
        return Ok(());
    }
    anyhow::ensure!(
        allow_insecure_http && url.scheme() == "http" && is_loopback_host(url.host_str()),
        "targets must use HTTPS; plain HTTP requires --allow-insecure-http and a loopback host"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn insecure_network_target_is_rejected() {
        assert!(validate_target_url(&Url::parse("http://example.com").unwrap(), true).is_err());
        validate_target_url(&Url::parse("http://127.0.0.1:8000").unwrap(), true).unwrap();
    }

    #[test]
    fn a_desktop_install_can_pair_with_its_own_workspace() {
        // Lemma Desktop serves its workspace and API on app.lemma.localhost.
        // Accepting only the three literal loopback spellings meant the host
        // refused the very workspace that had just handed it a pairing code,
        // so "Connect this computer" could never succeed in local mode.
        validate_target_url(
            &Url::parse("http://app.lemma.localhost:52502").unwrap(),
            true,
        )
        .unwrap();
        // Still opt-in: plain HTTP without the flag is refused wherever it points.
        assert!(
            validate_target_url(
                &Url::parse("http://app.lemma.localhost:52502").unwrap(),
                false
            )
            .is_err()
        );
    }

    #[test]
    fn only_a_real_localhost_suffix_counts_as_loopback() {
        assert!(is_loopback_host(Some("app.lemma.localhost")));
        assert!(is_loopback_host(Some("localhost")));
        // A name someone else can own must not pass because it merely contains
        // the word.
        assert!(!is_loopback_host(Some("localhost.attacker.example")));
        assert!(!is_loopback_host(Some("notlocalhost")));
        assert!(!is_loopback_host(None));
    }

    /// A name that resolves to loopback is loopback, whatever it is spelled.
    ///
    /// The spelling list is what broke pairing: Lemma Desktop moved off
    /// `*.localhost`, because a browser derives no registrable domain from it
    /// and a framed pod app can then hold no session, and started serving
    /// `app.127.0.0.1.sslip.io`. That resolves to 127.0.0.1 and is served by a
    /// backend bound there, and the list did not have it -- so the host refused
    /// to pair with the workspace that asked it to, silently.
    ///
    /// Needs a resolver, so it is skipped where there is none rather than
    /// failing: the assertion is about what this function concludes from an
    /// answer, not about the machine having one.
    #[test]
    fn a_name_that_answers_loopback_is_loopback_however_it_is_spelled() {
        use std::net::ToSocketAddrs;

        let resolvable = |host: &str| (host, 0u16).to_socket_addrs().is_ok();

        if resolvable("app.127.0.0.1.sslip.io") {
            assert!(
                is_loopback_host(Some("app.127.0.0.1.sslip.io")),
                "the domain a shipped install serves itself on must be loopback"
            );
        }
        // Somebody else's machine, spelled the same way. This is why the check
        // reads the address rather than the shape of the name.
        if resolvable("app.10.0.0.7.sslip.io") {
            assert!(!is_loopback_host(Some("app.10.0.0.7.sslip.io")));
        }
        // A public name stays refused whether or not it resolves here.
        assert!(!is_loopback_host(Some("lemma.work")));
    }

}
