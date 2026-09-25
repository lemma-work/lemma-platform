use super::*;

pub(super) fn compose_backend_environment(
    mut operator: HashMap<String, String>,
    infrastructure: Option<HashMap<String, String>>,
    agent_host_config: &Path,
) -> HashMap<String, String> {
    if let Some(infrastructure) = infrastructure {
        // Infrastructure endpoints describe the currently running private
        // runtime and must win over the static loopback defaults rendered into
        // the host pack. Reapplying operator configuration must never discard
        // these addresses.
        operator.extend(infrastructure);
    }
    // Where this Mac's own Agent Host keeps its config. The backend reads the
    // host ids of its pairings from it -- and nothing else -- to tell this
    // machine's host apart from any other host paired to it: only a user
    // paired to *this* host gets the loopback relay onto this Mac. A path,
    // not the ids, because the host writes them when it pairs, which is
    // usually after the backend started.
    operator.insert(
        "DESKTOP_AGENT_HOST_CONFIG_PATH".into(),
        agent_host_config.to_string_lossy().into_owned(),
    );
    operator
}

/// The handoff cap a shared installation runs with.
///
/// The backend's own default for `desktop_auth_create_limit`. Restated here
/// rather than left to that default, because the host pack has already
/// overridden it to 0 and an override is only undone by another override.
const SHARED_DESKTOP_AUTH_CREATE_LIMIT: u32 = 100;

pub(crate) fn sharing_environment(
    origin: &str,
    mode: SharingMode,
    who_can_join: WhoCanJoin,
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
        // A number, not a flag, and the one control this overlay missed. The
        // host pack sets it to 0, which the backend documents as "disable the
        // application-level cap" -- so a shared installation had an unbounded
        // desktop-auth-handoff endpoint, and anyone who reached the address
        // could create handoff records without limit.
        (
            "DESKTOP_AUTH_CREATE_LIMIT".into(),
            SHARED_DESKTOP_AUTH_CREATE_LIMIT.to_string(),
        ),
        ("DEBUG".into(), "false".into()),
        // The installation is reachable by people other than this Mac's user.
        //
        // `ENVIRONMENT` stays `local` -- it chooses storage, embeddings and key
        // handling that do not change with who can connect -- so it cannot be
        // what tells the backend to drop the relaxations it grants a machine
        // nobody else can reach: model providers on this Mac's loopback (which
        // would let any member aim the backend at Ollama, a dev server or
        // Lemma's own ports), the loopback CORS defaults, and the
        // configuration block `/health` shows the scenario suite. This does.
        ("INSTALLATION_SHARED".into(), "true".into()),
        // Who may create an account, now that somebody other than this Mac's user
        // can reach the sign-up page. Written in both directions rather than
        // only when narrowing: the backend's own Desktop default is already
        // invite-only, but an explicit value is what makes this overlay the
        // single place that decides, and what a reader of the running
        // environment can check.
        ("SIGNUP_MODE".into(), who_can_join.signup_mode().into()),
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

/// What activation checks through the shared origin before sharing commits.
///
/// Both halves of the gateway, because either can be what is broken: the
/// frontend's runtime config, and a real API call. The API call is the ALTCHA
/// challenge a sign-in asks for first, so a shared stack that could not sign
/// anybody in -- which is what a missing ALTCHA key produced -- fails here and
/// is rolled back, instead of being committed and discovered by a visitor.
pub(crate) const ACTIVATION_PROBES: [&str; 2] = [
    "/runtime-config.js",
    "/_lemma/api/auth/altcha/challenge?purpose=signin-risk",
];

/// Whether an activation probe's answer shows that path working.
///
/// The challenge must say ALTCHA is on: the overlay turns it on, so an answer
/// of `{"enabled": false}` is a backend that did not pick the overlay up.
pub(crate) fn activation_probe_passed(path: &str, status: u16, body: &str) -> bool {
    if !(200..300).contains(&status) {
        return false;
    }
    if !path.starts_with("/_lemma/api/") {
        return true;
    }
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|challenge| challenge.get("enabled").and_then(Value::as_bool))
        == Some(true)
}

pub(super) fn validate_canonical_origin(
    origin: &str,
    probe_token: &str,
    provider: Option<crate::sharing::TunnelProvider>,
) -> io::Result<()> {
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
    let origin = origin.trim_end_matches('/');
    for path in ACTIVATION_PROBES {
        let target = format!("{origin}{path}");
        let mut last_error;
        loop {
            // The gateway is held until this passes; the token is how locald's
            // own check gets through it.
            let mut request = client
                .get(&target)
                .header(crate::sharing::ACTIVATION_PROBE_HEADER, probe_token);
            if provider == Some(crate::sharing::TunnelProvider::Ngrok) {
                // ngrok's free tier puts an HTML interstitial in front of the
                // first browser request, and this header is its documented way
                // past for a non-browser client.
                request = request.header("ngrok-skip-browser-warning", "1");
            }
            match request.send() {
                Ok(response) => {
                    let status = response.status().as_u16();
                    let body = response.text().unwrap_or_default();
                    if activation_probe_passed(path, status, &body) {
                        break;
                    }
                    last_error = format!("{path}: HTTP {status}");
                }
                Err(error) => last_error = format!("{path}: {error}"),
            }
            if std::time::Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    format!("the shared canonical origin did not become healthy: {last_error}"),
                ));
            }
            thread::sleep(std::time::Duration::from_millis(500));
        }
    }
    Ok(())
}
