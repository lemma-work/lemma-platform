from __future__ import annotations

import pytest

from lemma_stack.config import store
from lemma_stack.output import AdminError


def test_new_document_generates_installation_secret(paths):
    doc = store.load_or_create(paths)
    assert len(store.installation_secret(doc)) == 32
    # stat mode 0600
    assert paths.config_file.stat().st_mode & 0o777 == 0o600


def test_an_existing_installation_keeps_its_pre_rename_secret(paths):
    """Regenerating this would be silent data loss: the same seed derives the
    key that encrypts stored secrets, so a new one leaves every encrypted row
    in that installation unreadable."""
    doc = store.load_or_create(paths)
    del doc["internal"]["installation_secret"]
    doc["internal"]["agentbox_api_key"] = "a" * 32

    assert store.installation_secret(doc) == "a" * 32


def test_upper_snake_keys_route_to_backend_env(paths):
    doc = store.load_or_create(paths)
    parts = store.set_value(doc, "LEMMA_OPENAI_API_KEY", "sk-123")
    assert parts == ("backend", "env", "LEMMA_OPENAI_API_KEY")
    assert store.env_overrides(doc, "backend")["LEMMA_OPENAI_API_KEY"] == "sk-123"


def test_dotted_keys_route_to_sections_and_coerce(paths):
    doc = store.load_or_create(paths)
    store.set_value(doc, "ports.backend", "9000")
    store.set_value(doc, "features.observability", "true")
    assert store.port(doc, "backend") == 9000
    assert store.feature(doc, "observability") is True


def test_env_values_stay_verbatim_strings(paths):
    doc = store.load_or_create(paths)
    store.set_value(doc, "backend.env.SOME_NUMERIC_TOKEN", "12345")
    assert store.env_overrides(doc, "backend")["SOME_NUMERIC_TOKEN"] == "12345"


def test_get_unset_key_raises(paths):
    doc = store.load_or_create(paths)
    with pytest.raises(AdminError):
        store.get_value(doc, "backend.env.NOPE")


def test_unset_removes_value(paths):
    doc = store.load_or_create(paths)
    store.set_value(doc, "LEMMA_OPENAI_API_KEY", "sk-123")
    store.unset_value(doc, "LEMMA_OPENAI_API_KEY")
    assert "LEMMA_OPENAI_API_KEY" not in store.env_overrides(doc, "backend")


def test_redact_only_secretish_keys():
    assert store.redact("LEMMA_OPENAI_API_KEY", "sk-1") == "********"
    assert store.redact("installation_secret", "abc") == "********"
    assert store.redact("SMTP_PASSWORD", "x") == "********"
    assert store.redact("ports.frontend", 3711) == 3711
    assert store.redact("WEB_SEARCH_PROVIDER", "duckduckgo") == "duckduckgo"


def test_comments_survive_rewrite(paths):
    doc = store.load_or_create(paths)
    text = paths.config_file.read_text()
    assert "# Edit values here" in text
    store.set_value(doc, "ports.frontend", "3712")
    store.save(paths, doc)
    assert "# Edit values here" in paths.config_file.read_text()


def test_invalid_provider_rejected(paths):
    doc = store.load_or_create(paths)
    store.set_value(doc, "runtime.provider", "containerd")
    with pytest.raises(AdminError):
        store.provider(doc)
