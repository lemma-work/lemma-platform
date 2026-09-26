//! Server setup's Test buttons, as the store answers them.

use super::*;
use crate::setup_probe::SetupService;

impl OperatorConfigStore {
    /// Test one capability, writing nothing.
    ///
    /// `service` is `ai` or one of [`SetupService`]'s names. A typed
    /// credential is tested as typed; with none, the stored one is, so a
    /// person can check what is already saved without the page ever holding
    /// it. The AI provider follows `probe_key`'s rule: a stored key goes only
    /// to the destination it was saved for.
    pub fn test_setup(&self, request: Value) -> io::Result<Value> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct TestRequest {
            service: String,
            #[serde(default)]
            ai: Option<AiProfile>,
            #[serde(default)]
            api_key: Option<String>,
            #[serde(default)]
            credential: Option<String>,
            #[serde(default)]
            from_email: Option<String>,
        }

        let request: TestRequest = serde_json::from_value(request)
            .map_err(|error| invalid(format!("invalid test request: {error}")))?;
        let saved = self
            .config
            .lock()
            .expect("operator config poisoned")
            .clone();
        if request.service == "ai" {
            let ai = request.ai.unwrap_or_else(|| saved.ai.clone());
            let api_key = self.probe_key(&ai, request.api_key)?;
            let models = self.provider_probe.discover(&ai, api_key.as_deref())?;
            let model = if ai.default_model.is_empty() {
                models[0].clone()
            } else if models.contains(&ai.default_model) {
                ai.default_model.clone()
            } else {
                return Err(invalid(format!(
                    "the provider no longer offers {:?}",
                    ai.default_model
                )));
            };
            self.provider_probe
                .complete(&ai, api_key.as_deref(), &model)?;
            let count = models.len();
            let plural = if count == 1 { "" } else { "s" };
            return Ok(json!({
                "detail": format!("Found {count} model{plural}, and {model} answered."),
                "models": models,
            }));
        }

        let service = SetupService::parse(&request.service)?;
        let credential = match request.credential.filter(|value| !value.trim().is_empty()) {
            Some(value) => value,
            None => self
                .vault
                .get(&saved.install_id, service.secret_name())?
                .filter(|value| !value.is_empty())
                .ok_or_else(|| invalid("nothing is saved to test yet; enter a key first"))?,
        };
        let from_email = request
            .from_email
            .unwrap_or_else(|| saved.email.from_email.clone());
        let detail = self.setup_probe.check(service, &credential, &from_email)?;
        Ok(json!({ "detail": detail }))
    }
}
