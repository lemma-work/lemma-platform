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


def test_a_pre_rename_override_is_applied_under_the_new_name(paths):
    """The backend refuses to start on a renamed name rather than ignoring it,
    so passing one through verbatim would turn somebody's existing override
    into a stack that will not boot."""
    from lemma_stack.config import render

    doc = store.load_or_create(paths)
    store.set_value(doc, "AGENTBOX_HOST_ALIAS", "host.example.test")

    resolved = render._user_backend_overrides(doc)

    assert resolved == {"WORKSPACE_HOST_ALIAS": "host.example.test"}
    assert "AGENTBOX_HOST_ALIAS" not in resolved


def test_an_explicit_new_name_wins_over_a_translated_old_one(paths):
    from lemma_stack.config import render

    doc = store.load_or_create(paths)
    doc["agentbox"] = {"env": {"AGENTBOX_HOST_ALIAS": "from-the-old-name"}}
    store.set_value(doc, "WORKSPACE_HOST_ALIAS", "chosen-deliberately")

    assert (
        render._user_backend_overrides(doc)["WORKSPACE_HOST_ALIAS"]
        == "chosen-deliberately"
    )


def test_the_pre_rename_env_section_still_reaches_the_backend(paths):
    """[agentbox.env] was never a separate namespace -- it and [backend.env]
    always landed in the one backend environment. Configs written before the
    rename still carry it, and dropping the read would silently discard
    whatever the user put there."""
    doc = store.load_or_create(paths)
    doc["agentbox"] = {"env": {"WORKSPACE_HOST_ALIAS": "from-agentbox"}}

    assert store.env_overrides(doc, "agentbox") == {
        "WORKSPACE_HOST_ALIAS": "from-agentbox"
    }


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
