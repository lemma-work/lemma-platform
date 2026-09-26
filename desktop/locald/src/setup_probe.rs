//! Server setup's Test buttons: one small, read-only request per service,
//! made from here rather than from the page.
//!
//! From here because the credential being tested is usually the one in the
//! vault, which the page can never read back, and because each request goes
//! to one fixed host the service publishes -- never an address the caller
//! chose -- so a stored key cannot be pointed anywhere else by asking for a
//! test. The AI provider is the exception, and is tested through
//! `provider_probe`, which holds its own rules about where it may connect.

use std::io::{self, Read};
use std::time::Duration;

use reqwest::blocking::{Client, RequestBuilder};
use reqwest::redirect::Policy;
use serde_json::Value;

const MAX_RESPONSE_BYTES: u64 = 256 * 1024;

/// A service Server setup can test, with the vault name of the credential it
/// uses when the page sends none.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SetupService {
    Composio,
    Telegram,
    Slack,
    Deepgram,
    Brave,
    Resend,
    Gemini,
}

impl SetupService {
    pub(crate) fn parse(name: &str) -> io::Result<Self> {
        Ok(match name {
            "composio" => Self::Composio,
            "telegram" => Self::Telegram,
            "slack" => Self::Slack,
            "deepgram" => Self::Deepgram,
            "brave" => Self::Brave,
            "resend" => Self::Resend,
            "gemini" => Self::Gemini,
            other => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("there is no test for {other:?}"),
                ))
            }
        })
    }

    pub(crate) fn secret_name(self) -> &'static str {
        match self {
            Self::Composio => "integrations.composio_api_key",
            Self::Telegram => "surfaces.telegram_bot_token",
            Self::Slack => "surfaces.slack_app_token",
            Self::Deepgram => "integrations.deepgram_api_key",
            Self::Brave => "integrations.brave_search_api_key",
            Self::Resend => "surfaces.resend_api_key",
            Self::Gemini => "integrations.gemini_api_key",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Composio => "Composio",
            Self::Telegram => "Telegram",
            Self::Slack => "Slack",
            Self::Deepgram => "Deepgram",
            Self::Brave => "Brave Search",
            Self::Resend => "Resend",
            Self::Gemini => "Google AI Studio",
        }
    }
}

/// Where each service answers. Real hosts in the app; a local stand-in in
/// tests, which is the only reason these are not constants.
#[derive(Clone, Debug)]
pub(crate) struct SetupEndpoints {
    pub(crate) composio: String,
    pub(crate) telegram: String,
    pub(crate) slack: String,
    pub(crate) deepgram: String,
    pub(crate) brave: String,
    pub(crate) resend: String,
    pub(crate) gemini: String,
}

impl Default for SetupEndpoints {
    fn default() -> Self {
        Self {
            composio: "https://backend.composio.dev".into(),
            telegram: "https://api.telegram.org".into(),
            slack: "https://slack.com".into(),
            deepgram: "https://api.deepgram.com".into(),
            brave: "https://api.search.brave.com".into(),
            resend: "https://api.resend.com".into(),
            gemini: "https://generativelanguage.googleapis.com".into(),
        }
    }
}

pub(crate) trait SetupProbe: Send + Sync {
    /// Check `credential` against `service`. `Ok` carries one line to show;
    /// `Err` one line that never contains the credential.
    fn check(
        &self,
        service: SetupService,
        credential: &str,
        from_email: &str,
    ) -> io::Result<String>;
}

pub(crate) struct HttpSetupProbe {
    pub(crate) endpoints: SetupEndpoints,
}

impl SetupProbe for HttpSetupProbe {
    fn check(
        &self,
        service: SetupService,
        credential: &str,
        from_email: &str,
    ) -> io::Result<String> {
        let credential = credential.trim();
        if credential.is_empty() {
            return Err(invalid(format!("enter a {} key first", service.label())));
        }
        if credential
            .chars()
            .any(|c| c.is_control() || c.is_whitespace())
        {
            return Err(invalid(format!(
                "that does not look like a {} key",
                service.label()
            )));
        }
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(15))
            .redirect(Policy::none())
            .no_proxy()
            .build()
            .map_err(|_| io::Error::other("could not prepare the test request"))?;
        let endpoints = &self.endpoints;
        match service {
            SetupService::Telegram => {
                // The token is part of the path, which is why no error below
                // ever carries the URL.
                let url = format!("{}/bot{credential}/getMe", endpoints.telegram);
                let (status, body) = send(client.get(url))?;
                let payload = json_body(&body);
                if status == 401 || status == 404 {
                    return Err(rejected(service));
                }
                if payload.get("ok").and_then(Value::as_bool) != Some(true) {
                    return Err(failed(service, status));
                }
                let name = payload
                    .pointer("/result/username")
                    .and_then(Value::as_str)
                    .unwrap_or("your bot");
                Ok(format!("Connected as @{name}."))
            }
            SetupService::Slack => {
                // The app-level token is what Socket Mode connects with, and
                // asking Slack for a connection URL is the one request that
                // proves it: the URL is simply not used.
                let url = format!("{}/api/apps.connections.open", endpoints.slack);
                let (status, body) = send(client.post(url).bearer_auth(credential))?;
                let payload = json_body(&body);
                if payload.get("ok").and_then(Value::as_bool) != Some(true) {
                    return match payload.get("error").and_then(Value::as_str) {
                        Some(
                            "invalid_auth"
                            | "not_authed"
                            | "account_inactive"
                            | "token_revoked"
                            | "not_allowed_token_type",
                        ) => Err(rejected(service)),
                        _ => Err(failed(service, status)),
                    };
                }
                Ok("Slack accepted the app-level token; Socket Mode can connect.".into())
            }
            SetupService::Deepgram => {
                let url = format!("{}/v1/projects", endpoints.deepgram);
                let (status, _) = send(
                    client
                        .get(url)
                        .header("Authorization", format!("Token {credential}")),
                )?;
                expect_success(service, status)?;
                Ok("Deepgram accepted the key.".into())
            }
            SetupService::Composio => {
                let url = format!("{}/api/v3/toolkits?limit=1", endpoints.composio);
                let (status, _) = send(client.get(url).header("x-api-key", credential))?;
                expect_success(service, status)?;
                Ok("Composio accepted the key.".into())
            }
            SetupService::Brave => {
                let url = format!("{}/res/v1/web/search?q=lemma&count=1", endpoints.brave);
                let (status, _) = send(
                    client
                        .get(url)
                        .header("X-Subscription-Token", credential)
                        .header("Accept", "application/json"),
                )?;
                expect_success(service, status)?;
                Ok("Brave Search accepted the key.".into())
            }
            SetupService::Gemini => {
                // A header, not the `?key=` form, so the key is never part of
                // a URL that could end up in an error.
                let url = format!("{}/v1beta/models?pageSize=1", endpoints.gemini);
                let (status, _) = send(client.get(url).header("x-goog-api-key", credential))?;
                match status {
                    200..=299 => Ok("Google accepted the key; voice calls can connect.".into()),
                    400 | 401 | 403 => Err(rejected(service)),
                    _ => Err(failed(service, status)),
                }
            }
            SetupService::Resend => {
                let url = format!("{}/domains", endpoints.resend);
                let (status, body) = send(client.get(url).bearer_auth(credential))?;
                let payload = json_body(&body);
                // A key limited to sending cannot list domains, and sending is
                // all Lemma asks of it.
                if status == 401
                    && payload.get("name").and_then(Value::as_str) == Some("restricted_api_key")
                {
                    return Ok(
                        "Resend accepted the key. It can only send, which is all Lemma needs."
                            .into(),
                    );
                }
                expect_success(service, status)?;
                Ok(resend_domain_line(&payload, from_email))
            }
        }
    }
}

/// What the domain list says about the sender the page is about to use.
pub(crate) fn resend_domain_line(payload: &Value, from_email: &str) -> String {
    let domains: Vec<(String, String)> = payload
        .get("data")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|domain| {
            Some((
                domain.get("name")?.as_str()?.to_ascii_lowercase(),
                domain
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned(),
            ))
        })
        .collect();
    let sender_domain = from_email
        .rsplit_once('@')
        .map(|(_, domain)| domain.trim().to_ascii_lowercase())
        .unwrap_or_default();
    if sender_domain.is_empty() {
        return "Resend accepted the key.".into();
    }
    match domains.iter().find(|(name, _)| *name == sender_domain) {
        Some((_, status)) if status == "verified" => {
            format!("Resend accepted the key, and {sender_domain} is verified.")
        }
        Some((_, status)) => format!(
            "Resend accepted the key, but {sender_domain} is {status}, not verified. Mail from it will be refused until it is."
        ),
        None => format!(
            "Resend accepted the key, but {sender_domain} is not one of its domains. Add and verify it in Resend, or send from a domain that is."
        ),
    }
}

fn send(request: RequestBuilder) -> io::Result<(u16, Vec<u8>)> {
    let response = request
        .header("User-Agent", "Lemma-Server-Setup-Test/1")
        .send()
        .map_err(|error| {
            // Never the reqwest error itself: it carries the URL, and for
            // Telegram the URL carries the token.
            io::Error::other(if error.is_timeout() {
                "the service did not answer in time"
            } else if error.is_connect() {
                "could not reach the service; check this Mac's internet connection"
            } else {
                "the test request failed"
            })
        })?;
    let status = response.status().as_u16();
    let mut body = Vec::new();
    response.take(MAX_RESPONSE_BYTES).read_to_end(&mut body)?;
    Ok((status, body))
}

fn json_body(body: &[u8]) -> Value {
    serde_json::from_slice(body).unwrap_or(Value::Null)
}

fn expect_success(service: SetupService, status: u16) -> io::Result<()> {
    match status {
        200..=299 => Ok(()),
        401 | 403 => Err(rejected(service)),
        _ => Err(failed(service, status)),
    }
}

fn rejected(service: SetupService) -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        format!("{} rejected the key.", service.label()),
    )
}

fn failed(service: SetupService, status: u16) -> io::Error {
    io::Error::other(match status {
        429 => format!(
            "{} is rate limiting this key; try again shortly.",
            service.label()
        ),
        _ => format!(
            "{} did not accept the test (HTTP {status}).",
            service.label()
        ),
    })
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;
    use std::thread;

    /// One canned answer from a loopback stand-in, and the request it saw.
    fn stand_in(status: &str, body: &'static str) -> (String, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let status = status.to_owned();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let count = stream.read(&mut request).unwrap();
            write!(
                stream,
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
            String::from_utf8_lossy(&request[..count]).into_owned()
        });
        (format!("http://{address}"), server)
    }

    fn probe_at(base: &str) -> HttpSetupProbe {
        HttpSetupProbe {
            endpoints: SetupEndpoints {
                composio: base.into(),
                telegram: base.into(),
                slack: base.into(),
                deepgram: base.into(),
                brave: base.into(),
                resend: base.into(),
                gemini: base.into(),
            },
        }
    }

    #[test]
    fn telegram_names_the_bot_it_connected_as() {
        let (base, server) = stand_in("200 OK", r#"{"ok":true,"result":{"username":"lemma_bot"}}"#);
        let said = probe_at(&base)
            .check(SetupService::Telegram, "123:abc", "")
            .unwrap();
        assert_eq!(said, "Connected as @lemma_bot.");
        let request = crate::join_within(server, "the Telegram stand-in");
        assert!(request.starts_with("GET /bot123:abc/getMe HTTP/1.1"));
    }

    #[test]
    fn a_refused_telegram_token_is_not_repeated_in_the_error() {
        let (base, server) = stand_in("401 Unauthorized", r#"{"ok":false}"#);
        let error = probe_at(&base)
            .check(SetupService::Telegram, "123:secret-token", "")
            .unwrap_err()
            .to_string();
        assert_eq!(error, "Telegram rejected the key.");
        assert!(!error.contains("secret-token"));
        crate::join_within(server, "the Telegram stand-in");
    }

    #[test]
    fn slack_reads_ok_false_as_a_refusal_even_with_status_200() {
        let (base, server) = stand_in("200 OK", r#"{"ok":false,"error":"not_allowed_token_type"}"#);
        let error = probe_at(&base)
            .check(SetupService::Slack, "xoxb-1", "")
            .unwrap_err();
        assert_eq!(error.to_string(), "Slack rejected the key.");
        let request = crate::join_within(server, "the Slack stand-in");
        assert!(request.starts_with("POST /api/apps.connections.open HTTP/1.1"));
        assert!(request
            .to_ascii_lowercase()
            .contains("authorization: bearer xoxb-1"));
    }

    #[test]
    fn slack_accepts_an_app_level_token_that_can_open_a_connection() {
        let (base, server) = stand_in("200 OK", r#"{"ok":true,"url":"wss://example.test/link"}"#);
        let said = probe_at(&base)
            .check(SetupService::Slack, "xapp-1", "")
            .unwrap();
        assert!(said.starts_with("Slack accepted the app-level token"));
        crate::join_within(server, "the Slack stand-in");
    }

    #[test]
    fn deepgram_composio_and_brave_send_their_own_key_headers() {
        for (service, header) in [
            (SetupService::Deepgram, "authorization: token k-1"),
            (SetupService::Composio, "x-api-key: k-1"),
            (SetupService::Brave, "x-subscription-token: k-1"),
            (SetupService::Gemini, "x-goog-api-key: k-1"),
        ] {
            let (base, server) = stand_in("200 OK", "{}");
            probe_at(&base).check(service, "k-1", "").unwrap();
            let request = crate::join_within(server, "the stand-in");
            assert!(
                request.to_ascii_lowercase().contains(header),
                "{service:?} sent:\n{request}"
            );
        }
    }

    #[test]
    fn a_forbidden_key_reads_as_rejected() {
        let (base, server) = stand_in("403 Forbidden", "{}");
        let error = probe_at(&base)
            .check(SetupService::Deepgram, "k-1", "")
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
        crate::join_within(server, "the stand-in");
    }

    #[test]
    fn a_send_only_resend_key_passes() {
        let (base, server) = stand_in("401 Unauthorized", r#"{"name":"restricted_api_key"}"#);
        let said = probe_at(&base)
            .check(SetupService::Resend, "re_1", "me@example.com")
            .unwrap();
        assert!(said.starts_with("Resend accepted the key."));
        crate::join_within(server, "the Resend stand-in");
    }

    #[test]
    fn resend_says_whether_the_sender_domain_is_verified() {
        let payload = serde_json::json!({"data": [
            {"name": "example.com", "status": "verified"},
            {"name": "example.org", "status": "pending"},
        ]});
        assert!(
            resend_domain_line(&payload, "me@example.com").ends_with("example.com is verified.")
        );
        assert!(resend_domain_line(&payload, "me@example.org").contains("pending, not verified"));
        assert!(resend_domain_line(&payload, "me@example.net").contains("not one of its domains"));
    }

    #[test]
    fn an_empty_or_spaced_credential_is_refused_before_any_request() {
        let probe = probe_at("http://127.0.0.1:9");
        assert!(probe.check(SetupService::Slack, "  ", "").is_err());
        assert!(probe.check(SetupService::Slack, "a b", "").is_err());
        assert!(SetupService::parse("nope").is_err());
    }
}
