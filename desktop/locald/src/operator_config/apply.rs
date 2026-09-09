//! Changing the configuration: one section at a time, and putting it back
//! when part of it fails.

use super::*;

pub(crate) fn snapshot_value(
    config: OperatorConfig,
    secret_presence: BTreeMap<String, bool>,
) -> Value {
    json!({
        "schema": configuration_schema(),
        "readiness": readiness(&config, &secret_presence),
        "config": config,
        "secrets": secret_presence,
    })
}

/// Whether the AI profile is usable, given only whether its key is stored.
///
/// Split out from `readiness` so a caller that needs this one answer can pay for
/// the one secret it rests on. Reading the whole presence map to reach it means
/// a vault round trip per configured secret, and on macOS each stored item
/// carries its own access control — so the discarded lookups are not merely
/// wasted, they are a queue of authorisation prompts.
pub(crate) fn ai_ready(config: &OperatorConfig, api_key_present: bool) -> bool {
    config.ai.protocol != "unconfigured"
        && !config.ai.default_model.is_empty()
        && config.ai.last_validated_at_unix_ms.is_some()
        && (local_no_auth(&config.ai.base_url) || api_key_present)
}

pub(crate) fn readiness(config: &OperatorConfig, secrets: &BTreeMap<String, bool>) -> Value {
    let ai_ready = ai_ready(config, secrets.get("ai.api_key").copied().unwrap_or(false));
    json!({
        "ai": if ai_ready { "ready" } else { "needs_setup" },
        "integrations": if config.integrations.composio_enabled
            || !config.integrations.google_client_id.is_empty()
            || !config.integrations.microsoft_client_id.is_empty()
        { "configured" } else { "optional" },
        "surfaces": if config.surfaces.slack_socket_mode
            || config.surfaces.telegram_polling
            || !config.surfaces.teams_app_id.is_empty()
            || !config.surfaces.whatsapp_phone_number_id.is_empty()
        { "configured" } else { "optional" },
        "overall": if ai_ready { "ready" } else { "needs_ai_setup" },
    })
}

#[cfg(test)]
pub(crate) struct EchoModelProviderProbe;

#[cfg(test)]
impl ModelProviderProbe for EchoModelProviderProbe {
    fn discover(&self, profile: &AiProfile, _api_key: Option<&str>) -> io::Result<Vec<String>> {
        if profile.models.is_empty() {
            Err(invalid("test provider has no models"))
        } else {
            Ok(profile.models.clone())
        }
    }
}

impl OperatorConfigStore {
    pub fn snapshot(&self) -> io::Result<Value> {
        let config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        let secret_presence = self.secret_presence(&config)?;
        Ok(snapshot_value(config, secret_presence))
    }

    /// List the models a candidate provider offers, writing nothing.
    ///
    /// Takes the same `ai` shape `apply` does, plus an optional `api_key`. When
    /// no key is supplied the stored one is used, so an already-configured
    /// provider can be re-listed without the caller handling the secret at all.
    pub fn discover_models(&self, request: Value) -> io::Result<Vec<String>> {
        // Pair the destination and vault read with the same committed revision.
        let _write = self.writes.lock().expect("operator writes poisoned");
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct DiscoverRequest {
            ai: AiProfile,
            #[serde(default)]
            api_key: Option<String>,
        }

        let request: DiscoverRequest = serde_json::from_value(request)
            .map_err(|error| invalid(format!("invalid provider probe request: {error}")))?;
        if request.ai.protocol == "unconfigured" {
            return Err(invalid("choose a provider protocol first"));
        }
        validate_ai_shape(&request.ai)?;

        let saved = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        let api_key = match request.api_key {
            Some(value) if !value.is_empty() => Some(value),
            // An empty string is the page saying "no key", which is legitimate
            // for a loopback provider. Absent means "use whatever is stored".
            Some(_) => None,
            None => self.saved_provider_key(&saved, &request.ai)?,
        };
        if !local_no_auth(&request.ai.base_url) && api_key.is_none() {
            return Err(invalid("this AI provider requires an API key"));
        }
        self.provider_probe
            .discover(&request.ai, api_key.as_deref())
    }

    /// Replace only the AI profile, leaving everything else exactly as it is.
    ///
    /// `apply` takes a whole configuration, which means any caller wanting to
    /// change the model has to hold — and faithfully return — the sharing,
    /// integration, and surface settings too. That is a fine contract for the
    /// bundled settings page and a bad one to expose anywhere else: a caller
    /// that got it wrong would silently reset unrelated configuration. This
    /// takes the one section it is allowed to touch and merges it here.
    pub fn set_ai(&self, request: Value) -> io::Result<Value> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct SetAiRequest {
            ai: AiProfile,
            /// Absent leaves the stored key alone; empty clears it.
            #[serde(default)]
            api_key: Option<String>,
        }

        let request: SetAiRequest = serde_json::from_value(request)
            .map_err(|error| invalid(format!("invalid AI profile request: {error}")))?;
        let mut config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        config.ai = request.ai;

        let mut secrets = BTreeMap::new();
        if let Some(api_key) = request.api_key {
            secrets.insert(
                "ai.api_key".to_string(),
                Some(api_key).filter(|value| !value.is_empty()),
            );
        }
        self.apply(ApplyOperatorConfig { config, secrets })
    }

    pub fn apply(&self, mut request: ApplyOperatorConfig) -> io::Result<Value> {
        let _write = self.writes.lock().expect("operator writes poisoned");
        self.apply_locked(&mut request)
    }

    pub fn update(&self, update: OperatorConfigUpdate) -> io::Result<Value> {
        let _write = self.writes.lock().expect("operator writes poisoned");
        let mut request = match update {
            OperatorConfigUpdate::Legacy(request) => *request,
            OperatorConfigUpdate::Section(patch) => {
                let mut config = self
                    .config
                    .lock()
                    .expect("operator config poisoned")
                    .clone();
                config.revision = patch.expected_revision;
                let prefix = match patch.section {
                    ConfigSection::Ai(ai) => {
                        config.ai = ai;
                        "ai."
                    }
                    ConfigSection::Integrations(integrations) => {
                        config.integrations = integrations;
                        "integrations."
                    }
                    ConfigSection::Surfaces(surfaces) => {
                        config.surfaces = surfaces;
                        "surfaces."
                    }
                };
                let mut secrets = BTreeMap::new();
                for (name, action) in patch.secrets {
                    if !name.starts_with(prefix) || !SECRET_NAMES.contains(&name.as_str()) {
                        return Err(invalid(
                            "credential does not belong to the selected section",
                        ));
                    }
                    match action {
                        CredentialAction::Keep => {}
                        CredentialAction::Replace { value } if value.is_empty() => {
                            return Err(invalid(
                                "replacement credential must not be empty; use remove",
                            ));
                        }
                        CredentialAction::Replace { value } => {
                            secrets.insert(name, Some(value));
                        }
                        CredentialAction::Remove => {
                            secrets.insert(name, None);
                        }
                    }
                }
                ApplyOperatorConfig { config, secrets }
            }
        };
        self.apply_locked(&mut request)
    }

    pub(crate) fn saved_provider_key(
        &self,
        saved: &OperatorConfig,
        requested: &AiProfile,
    ) -> io::Result<Option<String>> {
        let key = self.vault.get(&saved.install_id, "ai.api_key")?;
        if key.is_some()
            && (saved.ai.protocol != requested.protocol
                || saved.ai.base_url.trim_end_matches('/')
                    != requested.base_url.trim_end_matches('/'))
        {
            return Err(invalid("provider destination changed; enter a credential for this destination or explicitly remove the saved key"));
        }
        Ok(key)
    }

    pub(crate) fn apply_locked(&self, request: &mut ApplyOperatorConfig) -> io::Result<Value> {
        let old_config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        if request.config.install_id != old_config.install_id {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "operator config install identity cannot be changed",
            ));
        }
        if request.config.revision != old_config.revision {
            return Err(io::Error::new(io::ErrorKind::AlreadyExists,
                "configuration conflict: settings changed since this draft was opened; discard and reload before saving"));
        }
        request.config.schema_version = CONFIG_SCHEMA_VERSION;
        request.config.revision = old_config.revision.saturating_add(1);
        validate_secret_changes(&request.secrets)?;
        validate_config_shape(&request.config)?;
        let provider_changed = provider_profile_changed(&old_config.ai, &request.config.ai)
            || request.secrets.contains_key("ai.api_key")
            || request.config.ai.last_validated_at_unix_ms.is_none();
        if request.config.ai.protocol == "unconfigured" {
            request.config.ai.last_validated_at_unix_ms = None;
        } else if provider_changed {
            let api_key = match request.secrets.get("ai.api_key") {
                Some(Some(value)) if !value.is_empty() => Some(value.clone()),
                Some(_) => None,
                None => self.saved_provider_key(&old_config, &request.config.ai)?,
            };
            if !local_no_auth(&request.config.ai.base_url) && api_key.is_none() {
                return Err(invalid("this AI provider requires an API key"));
            }
            let models = self
                .provider_probe
                .discover(&request.config.ai, api_key.as_deref())?;
            if models.is_empty() {
                return Err(invalid("this AI provider returned no models"));
            }
            if request.config.ai.default_model.is_empty() {
                request.config.ai.default_model = models[0].clone();
            }
            if !models.contains(&request.config.ai.default_model) {
                return Err(invalid(format!(
                    "default model {:?} was not returned by the provider",
                    request.config.ai.default_model
                )));
            }
            request.config.ai.models = models;
            request.config.ai.last_validated_at_unix_ms = Some(current_unix_ms()?);
        } else {
            request.config.ai.last_validated_at_unix_ms = old_config.ai.last_validated_at_unix_ms;
        }
        validate_config(&request.config)?;

        let old_secrets =
            self.read_secrets(&old_config, request.secrets.keys().map(String::as_str))?;
        for (name, value) in &request.secrets {
            if let Err(error) = set_secret(
                self.vault.as_ref(),
                &old_config.install_id,
                name,
                value.as_deref().filter(|value| !value.is_empty()),
            ) {
                return Err(with_restore_error(
                    error,
                    restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
                ));
            }
        }

        let presence = match self.secret_presence(&request.config) {
            Ok(presence) => presence,
            Err(error) => {
                return Err(with_restore_error(
                    error,
                    restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
                ));
            }
        };
        if let Err(error) = validate_capability_requirements(&request.config, &presence) {
            return Err(with_restore_error(
                error,
                restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
            ));
        }
        if let Err(error) =
            write_private_atomic(&self.path, &serde_json::to_vec_pretty(&request.config)?)
        {
            return Err(with_restore_error(
                error,
                restore_secrets(self.vault.as_ref(), &old_config.install_id, &old_secrets),
            ));
        }
        let config = request.config.clone();
        *self.config.lock().expect("operator config poisoned") = config.clone();
        Ok(snapshot_value(config, presence))
    }

    pub(crate) fn capture_state(&self) -> io::Result<OperatorConfigState> {
        let config = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        Ok(OperatorConfigState {
            secrets: self.read_secrets(&config, SECRET_NAMES.into_iter())?,
            config,
        })
    }

    pub(crate) fn restore_state(&self, state: OperatorConfigState) -> io::Result<Value> {
        let _write = self.writes.lock().expect("operator writes poisoned");
        validate_config(&state.config)?;
        let current = self.capture_state()?;
        if let Err(error) = restore_secrets(
            self.vault.as_ref(),
            &state.config.install_id,
            &state.secrets,
        ) {
            return Err(with_restore_error(
                error,
                restore_secrets(
                    self.vault.as_ref(),
                    &current.config.install_id,
                    &current.secrets,
                ),
            ));
        }
        let presence = match self.secret_presence(&state.config) {
            Ok(presence) => presence,
            Err(error) => {
                return Err(with_restore_error(
                    error,
                    restore_secrets(
                        self.vault.as_ref(),
                        &current.config.install_id,
                        &current.secrets,
                    ),
                ));
            }
        };
        if let Err(error) =
            write_private_atomic(&self.path, &serde_json::to_vec_pretty(&state.config)?)
        {
            return Err(with_restore_error(
                error,
                restore_secrets(
                    self.vault.as_ref(),
                    &current.config.install_id,
                    &current.secrets,
                ),
            ));
        }
        *self.config.lock().expect("operator config poisoned") = state.config.clone();
        Ok(snapshot_value(state.config, presence))
    }
}
