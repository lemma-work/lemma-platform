//! What a child process inherits, and what never reaches a log.

use super::*;

pub(crate) const INHERITED_ENVIRONMENT: [&str; 24] = [
    "PATH",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSH_AUTH_SOCK",
];

pub(crate) fn sensitive_key(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    ["password", "secret", "token", "api_key", "apikey"]
        .iter()
        .any(|marker| key.contains(marker))
}

impl HostProcessManager {
    pub fn set_backend_environment(&self, environment: HashMap<String, String>) {
        *self
            .backend_environment
            .lock()
            .expect("backend environment lock poisoned") = environment;
    }

    pub fn replace_service_environment(
        &self,
        service: &str,
        environment: HashMap<String, String>,
    ) -> HashMap<String, String> {
        let mut overlays = self
            .service_environment
            .lock()
            .expect("service environment lock poisoned");
        if environment.is_empty() {
            overlays.remove(service).unwrap_or_default()
        } else {
            overlays
                .insert(service.to_owned(), environment)
                .unwrap_or_default()
        }
    }

    pub fn service_environment(&self, service: &str) -> HashMap<String, String> {
        self.service_environment
            .lock()
            .expect("service environment lock poisoned")
            .get(service)
            .cloned()
            .unwrap_or_default()
    }

    pub(crate) fn redact_excerpt(&self, mut excerpt: String) -> String {
        let mut secrets = Vec::new();
        if let Some(runtime) = self.manifest.managed_runtime.as_ref() {
            secrets.push(runtime.credentials.postgres_password.as_str());
            secrets.push(runtime.credentials.redis_password.as_str());
        }
        for spec in &self.manifest.services {
            for (key, value) in &spec.env {
                if sensitive_key(key) && value.len() >= 8 {
                    secrets.push(value);
                }
            }
        }
        let backend = self
            .backend_environment
            .lock()
            .expect("backend environment lock poisoned");
        for (key, value) in backend.iter() {
            if sensitive_key(key) && value.len() >= 8 {
                secrets.push(value);
            }
        }
        secrets.sort_by_key(|value| std::cmp::Reverse(value.len()));
        secrets.dedup();
        for secret in secrets {
            excerpt = excerpt.replace(secret, "[redacted]");
        }
        excerpt
    }
}
