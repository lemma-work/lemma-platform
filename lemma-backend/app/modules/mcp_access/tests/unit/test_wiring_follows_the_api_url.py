"""The authorization services agree on the URL the API is served at.

The consent service writes it into every token as the resource, and the
verifier accepts a token only for that resource. One that kept the URL it first
saw, while another read the current one, refused every token as being for some
other resource -- which is how the datastore e2e shard failed whenever a test
that patched `settings.api_url` happened to build the verifier first.
"""

from app.core.config import settings
from app.modules.mcp_access.services import wiring


def test_consent_and_the_verifier_always_agree_on_the_resource(monkeypatch):
    monkeypatch.setattr(settings, "api_url", "https://api.one.test")
    wiring.consent_service()
    wiring.access_token_verifier()
    wiring.authorization_server()

    monkeypatch.setattr(settings, "api_url", "https://API.Two.test/")
    assert wiring.consent_service()._issuer == "https://api.two.test"
    assert wiring.access_token_verifier()._api_url == "https://api.two.test"
    assert wiring.authorization_server()._api_url == "https://api.two.test"
