use super::*;

pub(super) fn compose_backend_environment(
    mut operator: HashMap<String, String>,
    infrastructure: Option<HashMap<String, String>>,
) -> HashMap<String, String> {
    if let Some(infrastructure) = infrastructure {
        // Infrastructure endpoints describe the currently running private
        // runtime and must win over the static loopback defaults rendered into
        // the host pack. Reapplying operator configuration must never discard
        // these addresses.
        operator.extend(infrastructure);
    }
    operator
}

pub(super) fn sharing_environment(
    origin: &str,
    mode: SharingMode,
) -> (HashMap<String, String>, HashMap<String, String>) {
    let origin = origin.trim_end_matches('/');
    let api_url = format!("{origin}/_lemma/api");
    let auth_url = format!("{origin}/auth");
    let secure = mode == SharingMode::Public;
    let exact_origin = exact_origin_regex(origin);
    let backend = HashMap::from([
        ("API_URL".into(), api_url.clone()),
        ("FRONTEND_URL".into(), origin.into()),
        ("AUTH_FRONTEND_URL".into(), auth_url.clone()),
        ("AUTH_WEBSITE_BASE_PATH".into(), "/auth".into()),
        ("SUPERTOKENS_API_BASE_PATH".into(), "/auth".into()),
        (
            "SUPERTOKENS_API_GATEWAY_PATH".into(),
            "/_lemma/api/st".into(),
        ),
        (
            "SESSION_COOKIE_SECURE".into(),
            if secure { "true" } else { "false" }.into(),
        ),
        ("SESSION_COOKIE_SAME_SITE".into(), "lax".into()),
        ("SESSION_COOKIE_DOMAIN".into(), String::new()),
        // No app host is served through a tunnel, so stop claiming one.
        //
        // The gateway routes by *path* -- `/_lemma/api` to the backend, the rest
        // to the frontend -- so there is no host-based route for
        // `<slug>.apps.<domain>` and there cannot be one without wildcard DNS on
        // the tunnel. Left set, `public_app_url` kept handing visitors
        // `<slug>.apps.lemma.localhost`, which their browser resolves against
        // *their own* machine: not a dead link but one pointing somewhere else
        // entirely. Blank makes `public_app_url` return None and
        // `app_slug_from_host` decline to route, which is the truth.
        ("APP_BASE_DOMAIN".into(), String::new()),
        // ...and with no app origin, the app-origin API door is meaningless.
        // It aliases the whole API under `/_lemma` on whatever origin serves
        // user-authored HTML, and widens the refresh cookie to `Path=/` to make
        // that work. Neither is wanted on a public origin.
        ("APP_API_VIA_APP_ORIGIN".into(), "false".into()),
        ("AUTH_EMAIL_VERIFICATION_REQUIRED".into(), "false".into()),
        ("CORS_ORIGIN_REGEX".into(), exact_origin),
        // Raised, not merely rewritten.
        //
        // The host pack turns every abuse control off, which is right for an
        // installation only this Mac can reach. This overlay is applied when
        // that stops being true -- and it used to change URLs and nothing else,
        // so an installation reachable from the LAN or the open internet still
        // had no rate limit on sign-in, no ceiling on account creation, and no
        // ALTCHA. Anyone who found the address got unlimited, unthrottled
        // password guessing against the owner's account.
        //
        // `DEBUG` matters for the same reason: the backend's own config
        // validator explains that it makes every unhandled error answer with a
        // source-annotated traceback, and it is set unconditionally for local
        // mode.
        ("AUTH_ABUSE_PROTECTION_ENABLED".into(), "true".into()),
        ("AUTH_ALTCHA_ENABLED".into(), "true".into()),
        ("DEBUG".into(), "false".into()),
    ]);
    let frontend = HashMap::from([
        ("NEXT_PUBLIC_API_URL".into(), api_url),
        ("NEXT_PUBLIC_AUTH_URL".into(), auth_url),
        ("NEXT_PUBLIC_SITE_URL".into(), origin.into()),
        ("NEXT_PUBLIC_AUTH_WEBSITE_BASE_PATH".into(), "/auth".into()),
        (
            "NEXT_PUBLIC_SUPERTOKENS_API_BASE_PATH".into(),
            "/auth".into(),
        ),
        (
            "NEXT_PUBLIC_SUPERTOKENS_API_GATEWAY_PATH".into(),
            "/_lemma/api/st".into(),
        ),
        (
            "NEXT_PUBLIC_AUTH_DEFAULT_REDIRECT_URI".into(),
            format!("{origin}/"),
        ),
        ("NEXT_PUBLIC_SESSION_TOKEN_DOMAIN".into(), String::new()),
        (
            "NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED".into(),
            "false".into(),
        ),
    ]);
    (backend, frontend)
}

pub(super) fn exact_origin_regex(origin: &str) -> String {
    let mut escaped = String::with_capacity(origin.len() + 2);
    escaped.push('^');
    for character in origin.chars() {
        if matches!(
            character,
            '.' | '+' | '*' | '?' | '^' | '$' | '(' | ')' | '[' | ']' | '{' | '}' | '|' | '\\'
        ) {
            escaped.push('\\');
        }
        escaped.push(character);
    }
    escaped.push('$');
    escaped
}

pub(super) fn validate_canonical_origin(origin: &str) -> io::Result<()> {
    // no_proxy, like every other client in this crate. locald talks to the
    // stack it is itself supervising, and a proxy configured without a
    // `<local>` bypass would route that at something that has never heard of
    // it. This used to be true for free: before the desktop workspace, locald's
    // reqwest had no system-proxy feature to honour. Sharing one dependency
    // graph with the agent host and the shell means it does now, so the
    // intent has to be written down.
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .no_proxy()
        .build()
        .map_err(io::Error::other)?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(45);
    let target = format!("{}/runtime-config.js", origin.trim_end_matches('/'));
    let mut last_error = String::new();
    while std::time::Instant::now() < deadline {
        match client.get(&target).send() {
            Ok(response) if response.status().is_success() => return Ok(()),
            Ok(response) => last_error = format!("HTTP {}", response.status()),
            Err(error) => last_error = error.to_string(),
        }
        thread::sleep(std::time::Duration::from_millis(500));
    }
    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        format!("the shared canonical origin did not become healthy: {last_error}"),
    ))
}
