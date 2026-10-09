"""The cached authorization services keep the URL the API is served at.

Each is built once and reused, and each keeps the URL it was built with: the
consent service writes it into every token as the resource, and the verifier
accepts a token only for that resource. Built once for the whole process, one
first built while the URL was different refused every token for the real one.
"""

from app.core.config import settings
from app.modules.mcp_access.services import wiring


def test_the_verifier_is_reused_while_the_url_holds_and_rebuilt_when_it_changes(
    monkeypatch,
):
    monkeypatch.setattr(settings, "api_url", "https://api.one.test")
    first = wiring.access_token_verifier()
    assert wiring.access_token_verifier() is first

    monkeypatch.setattr(settings, "api_url", "https://api.two.test")
    second = wiring.access_token_verifier()
    assert second is not first
    assert second._api_url == "https://api.two.test"


def test_consent_and_the_verifier_always_agree_on_the_resource(monkeypatch):
    monkeypatch.setattr(settings, "api_url", "https://api.one.test")
    wiring.consent_service()
    wiring.access_token_verifier()

    monkeypatch.setattr(settings, "api_url", "https://API.Two.test/")
    assert wiring.consent_service()._issuer == "https://api.two.test"
    assert wiring.access_token_verifier()._api_url == "https://api.two.test"
    assert wiring.authorization_server()._api_url == "https://api.two.test"
