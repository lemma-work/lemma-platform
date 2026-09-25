//! One alias request: check it is ours, point it at the backend's app
//! ingress under the canonical host, and bring the answer back.

use std::str::FromStr;
use std::sync::Arc;

use hyper::client::HttpConnector;
use hyper::header::{HeaderValue, HOST, LOCATION};
use hyper::{Body, Client, Request, Response, StatusCode, Uri};

use crate::sharing::gateway::{is_websocket_upgrade, simple_response, strip_forwarding_headers};

/// What one alias listener fronts.
#[derive(Clone, Debug)]
pub struct AliasTarget {
    /// The workspace host the alias is served on: `app.lemma.localhost`.
    pub alias_host: String,
    pub alias_port: u16,
    /// The canonical app host: `orders.apps.lemma.localhost`.
    pub canonical_host: String,
    pub backend_port: u16,
}

impl AliasTarget {
    fn alias_authority(&self) -> String {
        format!("{}:{}", self.alias_host, self.alias_port)
    }

    fn canonical_authority(&self) -> String {
        format!("{}:{}", self.canonical_host, self.backend_port)
    }
}

/// Whether a request's `Host` names this alias.
///
/// Anything else is refused: a page that rebinds its own name to 127.0.0.1
/// reaches this port with its own `Host`, and it has no business here. Loopback
/// literals are allowed for diagnostics; the browser always sends the name.
pub(crate) fn host_is_alias(host: Option<&HeaderValue>, target: &AliasTarget) -> bool {
    let Some(host) = host.and_then(|value| value.to_str().ok()) else {
        return false;
    };
    let host = host.to_ascii_lowercase();
    let port = target.alias_port.to_string();
    let Some((name, given_port)) = host.rsplit_once(':') else {
        return false;
    };
    given_port == port && (name == target.alias_host || name == "127.0.0.1" || name == "localhost")
}

/// Rewrite an alias request onto the backend, in place.
///
/// `Host` becomes the canonical app host, so the backend's host routing picks
/// the app exactly as it does for the canonical URL. Forwarding headers a
/// client sent are dropped: nothing in front of this set them.
pub(crate) fn rewrite_request(request: &mut Request<Body>, target: &AliasTarget) -> Result<(), ()> {
    strip_forwarding_headers(request.headers_mut());
    let canonical = HeaderValue::from_str(&target.canonical_authority()).map_err(|_| ())?;
    request.headers_mut().insert(HOST, canonical);
    let path_and_query = request
        .uri()
        .path_and_query()
        .map_or("/", |value| value.as_str())
        .to_owned();
    let upstream = format!("http://127.0.0.1:{}{path_and_query}", target.backend_port);
    *request.uri_mut() = Uri::from_str(&upstream).map_err(|_| ())?;
    Ok(())
}

/// Keep a redirect to the app's own canonical origin inside the frame.
///
/// The backend's app ingress does not redirect today; this is here so that a
/// redirect it (or an app's own middleware) ever issues to its canonical
/// origin lands on the alias rather than on a host the frame cannot hold a
/// session on. Anything else -- a relative path, somebody else's origin -- is
/// left exactly as sent.
pub(crate) fn rewrite_location(response: &mut Response<Body>, target: &AliasTarget) {
    let Some(location) = response
        .headers()
        .get(LOCATION)
        .and_then(|value| value.to_str().ok())
    else {
        return;
    };
    let canonical = format!("http://{}", target.canonical_authority());
    let Some(rest) = location.strip_prefix(&canonical) else {
        return;
    };
    if !(rest.is_empty() || rest.starts_with(['/', '?', '#'])) {
        return;
    }
    let rewritten = format!("http://{}{rest}", target.alias_authority());
    if let Ok(value) = HeaderValue::from_str(&rewritten) {
        response.headers_mut().insert(LOCATION, value);
    }
}

pub(crate) async fn forward(
    mut request: Request<Body>,
    client: Client<HttpConnector, Body>,
    target: Arc<AliasTarget>,
) -> Result<Response<Body>, hyper::Error> {
    if !host_is_alias(request.headers().get(HOST), &target) {
        return Ok(simple_response(
            StatusCode::MISDIRECTED_REQUEST,
            "This address serves one Lemma app to this computer's own workspace.",
        ));
    }
    if rewrite_request(&mut request, &target).is_err() {
        return Ok(simple_response(
            StatusCode::BAD_GATEWAY,
            "invalid upstream request",
        ));
    }
    let websocket = is_websocket_upgrade(&request);
    let downstream_upgrade = websocket.then(|| hyper::upgrade::on(&mut request));
    let mut response = match client.request(request).await {
        Ok(response) => response,
        Err(_) => {
            return Ok(simple_response(
                StatusCode::BAD_GATEWAY,
                "Lemma is not answering on this computer yet.",
            ))
        }
    };
    if websocket && response.status() == StatusCode::SWITCHING_PROTOCOLS {
        let upstream_upgrade = hyper::upgrade::on(&mut response);
        if let Some(downstream_upgrade) = downstream_upgrade {
            tokio::spawn(async move {
                if let (Ok(mut downstream), Ok(mut upstream)) =
                    (downstream_upgrade.await, upstream_upgrade.await)
                {
                    let _ = tokio::io::copy_bidirectional(&mut downstream, &mut upstream).await;
                }
            });
        }
    }
    rewrite_location(&mut response, &target);
    Ok(response)
}
