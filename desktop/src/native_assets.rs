use tauri::Url;

#[derive(Clone, Copy)]
enum Protocol {
    Tauri,
    Windows,
}

impl Protocol {
    fn origin(self) -> &'static str {
        match self {
            Self::Tauri => "tauri://localhost",
            Self::Windows => "http://tauri.localhost",
        }
    }

    fn trusts(self, url: &Url) -> bool {
        let origin = Url::parse(self.origin()).expect("fixed native asset origin");
        url.scheme() == origin.scheme()
            && url.host_str() == origin.host_str()
            && url.port() == origin.port()
            && url.username().is_empty()
            && url.password().is_none()
    }
}

fn platform_protocol() -> Protocol {
    if cfg!(windows) {
        Protocol::Windows
    } else {
        Protocol::Tauri
    }
}

pub fn url(path: &str, development_port: Option<u16>) -> String {
    match development_port {
        Some(port) => format!("http://127.0.0.1:{port}/{path}"),
        None => format!("{}/{path}", platform_protocol().origin()),
    }
}

pub fn is_trusted(url: &Url, development_port: Option<u16>) -> bool {
    platform_protocol().trusts(url)
        || (development_port.is_some()
            && url.scheme() == "http"
            && matches!(url.host_str(), Some("127.0.0.1" | "localhost"))
            && url.port() == development_port
            && url.username().is_empty()
            && url.password().is_none())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_platforms_accept_their_bundled_setup_settings_and_confirmation() {
        for protocol in [Protocol::Tauri, Protocol::Windows] {
            for path in [
                "/",
                "/index.html?intent=quit",
                "/control.html",
                "/confirmation.html",
            ] {
                let url = Url::parse(&format!("{}{path}", protocol.origin())).unwrap();
                assert!(protocol.trusts(&url), "{url}");
            }
        }
    }

    #[test]
    fn native_trust_does_not_extend_to_remote_or_lookalike_origins() {
        for protocol in [Protocol::Tauri, Protocol::Windows] {
            for raw in [
                "https://lemma.work/index.html",
                "http://localhost/index.html",
                "http://127.0.0.1/index.html",
                "http://tauri.localhost:8080/index.html",
                "http://tauri.localhost.evil.example/index.html",
                "http://tauri.localhost@evil.example/index.html",
                "http://user@tauri.localhost/index.html",
                "https://tauri.localhost/index.html",
                "tauri://evil.example/control.html",
                "tauri://localhost:8080/control.html",
                "tauri://user@localhost/control.html",
            ] {
                assert!(!protocol.trusts(&Url::parse(raw).unwrap()), "{raw}");
            }
        }
        assert!(!Protocol::Tauri.trusts(&Url::parse(Protocol::Windows.origin()).unwrap()));
        assert!(!Protocol::Windows.trusts(&Url::parse(Protocol::Tauri.origin()).unwrap()));
    }

    #[test]
    fn development_origin_requires_an_explicit_exact_port() {
        let dev = Url::parse("http://127.0.0.1:1430/control.html").unwrap();
        assert!(is_trusted(&dev, Some(1430)));
        assert!(!is_trusted(&dev, Some(1431)));
        assert!(!is_trusted(&dev, None));
        assert!(is_trusted(
            &Url::parse(&url("index.html", None)).unwrap(),
            None
        ));
        assert_eq!(
            url("index.html", Some(1430)),
            "http://127.0.0.1:1430/index.html"
        );
    }
}
