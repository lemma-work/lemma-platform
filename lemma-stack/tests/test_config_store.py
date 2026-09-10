from __future__ import annotations

import pytest

from lemma_stack.config import store
from lemma_stack.output import AdminError


def test_new_document_generates_installation_secret(paths):
    doc = store.load_or_create(paths)
    assert len(store.installation_secret(doc)) == 32
    # stat mode 0600
    assert paths.config_file.stat().st_mode & 0o777 == 0o600


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


def test_a_config_from_a_newer_lemma_is_refused_rather_than_misread(paths):
    """`SCHEMA_VERSION` was declared and never compared with the file.

    So a config written by a newer lemma-stack parsed cleanly and was treated as
    this version's format -- whatever the newer one meant by a field was read as
    whatever this one means by it. Refusing is the only safe answer: the
    settings are not lost, they are described in a way this version cannot read.
    """
    doc = store.new_document()
    doc["schema"] = store.SCHEMA_VERSION + 1
    store.save(paths, doc)

    with pytest.raises(AdminError) as raised:
        store.load(paths)

    message = str(raised.value)
    assert "newer Lemma" in message
    assert str(store.SCHEMA_VERSION + 1) in message
    # It has to say what to do, because the file is the user's own settings.
    assert "Upgrade" in message


def test_the_current_schema_loads_and_a_missing_one_means_the_first(paths):
    store.save(paths, store.new_document())
    assert store.document_schema(store.load(paths)) == store.SCHEMA_VERSION

    without = store.new_document()
    del without["schema"]
    store.save(paths, without)
    # The template has always written it, so a file without one is hand-made or
    # predates the field; version 1 is what its contents mean either way.
    assert store.document_schema(store.load(paths)) == 1


@pytest.mark.parametrize("declared", ["two", 1.5, True, [1]])
def test_a_schema_that_is_not_a_version_is_refused(paths, declared):
    """A non-integer is a claim about the format, not a version. `True` is in
    here on purpose: it is an `int` in Python and would otherwise read as 1."""
    doc = store.new_document()
    doc["schema"] = declared
    store.save(paths, doc)

    with pytest.raises(AdminError, match="not a version number"):
        store.load(paths)


def test_a_migration_stamps_the_schema_it_migrated_to(tmp_path):
    """There is one version, so the step list is empty and `migrate` only
    stamps. The seam is what matters: a version 2 with nowhere to put its
    migration is how a config gets read as something it is not."""
    doc = store.new_document()
    del doc["schema"]

    migrated = store.migrate(doc, 1)

    assert migrated["schema"] == store.SCHEMA_VERSION
