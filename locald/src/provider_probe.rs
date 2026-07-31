use std::io::{self, Read};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, ToSocketAddrs};
use std::time::Duration;

use reqwest::blocking::{Client, RequestBuilder};
use reqwest::redirect::Policy;
use serde_json::Value;

use crate::operator_config::AiProfile;

const MAX_RESPONSE_BYTES: u64 = 1024 * 1024;

pub(crate) trait ModelProviderProbe: Send + Sync {
    fn discover(&self, profile: &AiProfile, api_key: Option<&str>) -> io::Result<Vec<String>>;
}

pub(crate) struct HttpModelProviderProbe;

impl ModelProviderProbe for HttpModelProviderProbe {
    fn discover(&self, profile: &AiProfile, api_key: Option<&str>) -> io::Result<Vec<String>> {
        let mut url = reqwest::Url::parse(&profile.base_url)
            .map_err(|error| invalid(format!("invalid AI provider URL: {error}")))?;
        validate_url(&url, profile.allow_private_network)?;
        let authority = resolve_and_validate(&url, profile.allow_private_network)?;
        {
            let mut segments = url
                .path_segments_mut()
                .map_err(|_| invalid("AI provider URL cannot be a base URL"))?;
            segments.pop_if_empty();
            segments.push("models");
        }

        let host = url
            .host_str()
            .ok_or_else(|| invalid("AI provider URL is missing a host"))?
            .to_owned();
        let mut client = Client::builder()
            .connect_timeout(Duration::from_secs(3))
            .timeout(Duration::from_secs(8))
            .redirect(Policy::none())
            .no_proxy();
        if host.parse::<IpAddr>().is_err() {
            client = client.resolve(&host, authority);
        }
        let client = client
            .build()
            .map_err(|error| io::Error::other(format!("provider HTTP client failed: {error}")))?;
        let request = authenticated_request(client.get(url), &profile.protocol, api_key)?;
        let response = request
            .header("Accept", "application/json")
            .header("User-Agent", "Lemma-Local-Provider-Validation/1")
            .send()
            .map_err(redacted_request_error)?;
        let status = response.status();
        if !status.is_success() {
            let message = match status.as_u16() {
                401 | 403 => "provider rejected the credential",
                404 => "provider does not expose a compatible model-list endpoint",
                429 => "provider rate limit or quota blocked validation",
                _ if status.is_redirection() => {
                    "provider redirected validation; redirects are disabled"
                }
                _ => "provider model-list request failed",
            };
            return Err(io::Error::other(format!(
                "{message} (HTTP {})",
                status.as_u16()
            )));
        }

        let mut limited = response.take(MAX_RESPONSE_BYTES + 1);
        let mut body = Vec::new();
        limited.read_to_end(&mut body)?;
        if body.len() as u64 > MAX_RESPONSE_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "provider response exceeded 1 MiB",
            ));
        }
        let payload: Value = serde_json::from_slice(&body).map_err(|_| {
            io::Error::new(io::ErrorKind::InvalidData, "provider returned invalid JSON")
        })?;
        let mut models: Vec<String> = payload
            .get("data")
            .or_else(|| payload.get("models"))
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|model| {
                model
                    .get("id")
                    .or_else(|| model.get("name"))
                    .and_then(Value::as_str)
            })
            .filter(|model| !model.is_empty() && model.len() <= 256)
            .map(str::to_owned)
            .collect();
        models.sort();
        models.dedup();
        if models.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "provider returned no usable models",
            ));
        }
        Ok(models)
    }
}

fn authenticated_request(
    request: RequestBuilder,
    protocol: &str,
    api_key: Option<&str>,
) -> io::Result<RequestBuilder> {
    match (protocol, api_key.filter(|value| !value.is_empty())) {
        ("openai_compat", Some(key)) => Ok(request.bearer_auth(key)),
        ("openai_compat", None) => Ok(request),
        ("anthropic_compat", Some(key)) => Ok(request
            .header("x-api-key", key)
            .header("anthropic-version", "2023-06-01")),
        ("anthropic_compat", None) => Ok(request),
        _ => Err(invalid("unsupported AI provider protocol")),
    }
}

fn validate_url(url: &reqwest::Url, allow_private_network: bool) -> io::Result<()> {
    validated_addresses(url, allow_private_network).map(|_| ())
}

fn validated_addresses(
    url: &reqwest::Url,
    allow_private_network: bool,
) -> io::Result<Vec<SocketAddr>> {
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid(
            "AI base URL must be HTTP(S), contain a host, and omit credentials, query, and fragment",
        ));
    }
    let addresses = resolved_addresses(url)?;
    let all_loopback = addresses.iter().all(|address| address.ip().is_loopback());
    let has_private = addresses
        .iter()
        .any(|address| !address.ip().is_loopback() && !is_public(address.ip()));
    if url.scheme() == "http" && !all_loopback && !allow_private_network {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "unencrypted non-loopback model endpoints require explicit network trust",
        ));
    }
    if has_private && !allow_private_network {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "private-network model endpoints require explicit network trust",
        ));
    }
    Ok(addresses)
}

fn resolve_and_validate(url: &reqwest::Url, allow_private_network: bool) -> io::Result<SocketAddr> {
    validated_addresses(url, allow_private_network)?
        .into_iter()
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "provider host did not resolve"))
}

fn resolved_addresses(url: &reqwest::Url) -> io::Result<Vec<SocketAddr>> {
    let host = url
        .host_str()
        .ok_or_else(|| invalid("AI provider URL is missing a host"))?;
    let port = url
        .port_or_known_default()
        .ok_or_else(|| invalid("AI provider URL is missing a port"))?;
    let addresses: Vec<_> = (host, port).to_socket_addrs()?.collect();
    if addresses.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "provider host did not resolve",
        ));
    }
    Ok(addresses)
}

fn is_public(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(address) => is_public_v4(address),
        IpAddr::V6(address) => is_public_v6(address),
    }
}

fn is_public_v4(address: Ipv4Addr) -> bool {
    let octets = address.octets();
    !address.is_private()
        && !address.is_loopback()
        && !address.is_link_local()
        && !address.is_broadcast()
        && !address.is_documentation()
        && !address.is_unspecified()
        && !address.is_multicast()
        && octets[0] != 0
        && !(octets[0] == 100 && (64..=127).contains(&octets[1]))
}

fn is_public_v6(address: Ipv6Addr) -> bool {
    let segments = address.segments();
    !(address.is_loopback()
        || address.is_unspecified()
        || address.is_multicast()
        || (segments[0] & 0xfe00) == 0xfc00
        || (segments[0] & 0xffc0) == 0xfe80
        || (segments[0] == 0x2001 && segments[1] == 0x0db8))
}

fn redacted_request_error(error: reqwest::Error) -> io::Error {
    let message = if error.is_timeout() {
        "provider request timed out"
    } else if error.is_connect() {
        "provider connection failed"
    } else if error.is_builder() {
        "provider request was invalid"
    } else {
        "provider request failed"
    };
    io::Error::other(message)
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

    #[test]
    fn blocks_private_and_cleartext_networks_without_explicit_trust() {
        let private = reqwest::Url::parse("http://192.168.10.4:1234/v1").unwrap();
        assert!(validate_url(&private, false).is_err());
        assert!(validate_url(&private, true).is_ok());
        let public_http = reqwest::Url::parse("http://93.184.216.34/v1").unwrap();
        assert!(validate_url(&public_http, false).is_err());
        let public_https = reqwest::Url::parse("https://93.184.216.34/v1").unwrap();
        assert!(validate_url(&public_https, false).is_ok());
    }

    #[test]
    fn discovers_models_from_a_bounded_loopback_response() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 2048];
            let count = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..count]);
            assert!(request.starts_with("GET /v1/models HTTP/1.1"));
            let body = r#"{"data":[{"id":"zeta"},{"id":"alpha"},{"id":"alpha"}]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
        });
        let profile = AiProfile {
            protocol: "openai_compat".into(),
            base_url: format!("http://{address}/v1"),
            allow_private_network: false,
            ..Default::default()
        };
        assert_eq!(
            HttpModelProviderProbe.discover(&profile, None).unwrap(),
            vec!["alpha", "zeta"]
        );
        server.join().unwrap();
    }
}
