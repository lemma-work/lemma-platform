//! What a shared installation is told about itself.
//!
//! Split out of `tests.rs` under DES-09. One subject: the environment overlay
//! that runs when the installation stops being reachable only from this Mac --
//! the URLs it rewrites, and the controls the local pack switched off that it
//! has to switch back on.

use super::environment::exact_origin_regex;
use super::sharing_environment;
use crate::sharing::SharingMode;

#[test]
fn public_canonical_environment_uses_one_prefixed_secure_origin() {
    let (backend, frontend) =
        sharing_environment("https://lemma.example.com/", SharingMode::Public);
    assert_eq!(backend["API_URL"], "https://lemma.example.com/_lemma/api");
    // A tunnel serves one origin and no app host, so the deployment must
    // stop advertising one. Left set, every app's URL pointed at
    // `<slug>.apps.lemma.localhost` -- which a visitor's browser resolves
    // against their own machine.
    assert_eq!(backend["APP_BASE_DOMAIN"], "");
    assert_eq!(backend["APP_API_VIA_APP_ORIGIN"], "false");
    assert_eq!(backend["FRONTEND_URL"], "https://lemma.example.com");
    assert_eq!(backend["SUPERTOKENS_API_GATEWAY_PATH"], "/_lemma/api/st");
    assert_eq!(backend["SESSION_COOKIE_SECURE"], "true");
    assert_eq!(backend["AUTH_EMAIL_VERIFICATION_REQUIRED"], "false");
    assert_eq!(
        backend["CORS_ORIGIN_REGEX"],
        "^https://lemma\\.example\\.com$"
    );
    assert_eq!(
        frontend["NEXT_PUBLIC_API_URL"],
        "https://lemma.example.com/_lemma/api"
    );
    assert_eq!(
        frontend["NEXT_PUBLIC_AUTH_URL"],
        "https://lemma.example.com/auth"
    );
    assert_eq!(
        frontend["NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED"],
        "false"
    );
}

#[test]
fn lan_canonical_environment_keeps_host_only_nonsecure_cookies() {
    let (backend, frontend) =
        sharing_environment("http://192.168.1.20:51234", SharingMode::LocalNetwork);
    assert_eq!(backend["SESSION_COOKIE_SECURE"], "false");
    assert_eq!(backend["SESSION_COOKIE_DOMAIN"], "");
    assert_eq!(frontend["NEXT_PUBLIC_SESSION_TOKEN_DOMAIN"], "");
    assert_eq!(
        exact_origin_regex("http://192.168.1.20:51234"),
        "^http://192\\.168\\.1\\.20:51234$"
    );
}

/// Exposing an installation raises its defences, in every mode.
///
/// The host pack turns every abuse control off, which is correct while only
/// this Mac can reach the stack. This overlay is what runs when that stops
/// being true, and it used to rewrite URLs and nothing else -- so a
/// workspace on the LAN or the open internet had no sign-in rate limit, no
/// ceiling on account creation, no ALTCHA, and answered unhandled errors
/// with a source-annotated traceback.
#[test]
fn sharing_raises_the_abuse_controls_the_local_pack_turns_off() {
    for (origin, mode) in [
        ("https://lemma.example.com", SharingMode::Public),
        ("http://192.168.1.20:51234", SharingMode::LocalNetwork),
    ] {
        let (backend, _) = sharing_environment(origin, mode);
        assert_eq!(
            backend["AUTH_ABUSE_PROTECTION_ENABLED"], "true",
            "{origin} is reachable by someone other than this Mac"
        );
        assert_eq!(backend["AUTH_ALTCHA_ENABLED"], "true", "{origin}");
        // A number, not a flag, which is why it was missed. The pack sets it
        // to 0, and the backend documents 0 as "disable the application-level
        // cap" -- so this used to leave a reachable installation with an
        // unbounded desktop-auth-handoff endpoint.
        let limit: u32 = backend["DESKTOP_AUTH_CREATE_LIMIT"]
            .parse()
            .expect("the handoff cap must be a number");
        assert!(
            limit > 0,
            "{origin} must not accept unlimited desktop auth handoffs"
        );
        assert_eq!(
            backend["DEBUG"], "false",
            "{origin} must not answer strangers with tracebacks"
        );
    }
}
