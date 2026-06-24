from __future__ import annotations

import io
import pathlib
import sys
import tokenize

import pytest


pytestmark = pytest.mark.unit


_LEMA_CONNECTORS_SRC = (
    pathlib.Path(__file__).resolve().parents[5]
    / "lemma-connectors"
    / "src"
    / "lemma_connectors"
)


def _ensure_lemma_connectors_on_path() -> None:
    src_root = str(_LEMA_CONNECTORS_SRC.parent)
    if src_root not in sys.path:
        sys.path.insert(0, src_root)


@pytest.fixture(autouse=True)
def _isolate_sys_modules():
    _ensure_lemma_connectors_on_path()
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("lemma_connectors"):
            del sys.modules[mod_name]
    yield
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("lemma_connectors"):
            del sys.modules[mod_name]


_CONNECTOR_PACKAGES = (
    "gmail",
    "google_calendar",
    "google_drive",
    "google_docs",
    "google_sheets",
    "slack",
    "jira",
)


def test_no_syntax_warnings_in_lemma_connectors_package():
    """Compiling every .py file in lemma_connectors must not emit
    SyntaxWarning for invalid escape sequences (Python 3.12+)."""
    invalid: list[str] = []
    valid_escapes = frozenset("\\\"'nrtabfv01234567xNuU\n")
    for path in _LEMA_CONNECTORS_SRC.rglob("*.py"):
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except Exception:
            continue
        for tok in tokens:
            if tok.type != tokenize.STRING:
                continue
            raw = tok.string
            prefix_end = 0
            while prefix_end < len(raw) and raw[prefix_end] in "rbRfFuU":
                prefix_end += 1
            if "r" in raw[:prefix_end] or "R" in raw[:prefix_end]:
                continue
            j = prefix_end
            while j < len(raw):
                if raw[j] == "\\":
                    nxt = raw[j + 1] if j + 1 < len(raw) else ""
                    if nxt not in valid_escapes:
                        invalid.append(f"{path}:{tok.start[0]}: \\{nxt!r}")
                        break
                    j += 2
                    continue
                j += 1
    assert not invalid, "Invalid escape sequences found:\n" + "\n".join(invalid[:20])


def test_importing_core_auth_does_not_load_any_connector_client():
    """import lemma_connectors.core.auth must not transitively import any
    of the 7 connector client modules."""
    import importlib

    importlib.import_module("lemma_connectors.core.auth")
    for pkg in _CONNECTOR_PACKAGES:
        assert f"lemma_connectors.{pkg}.client" not in sys.modules, (
            f"Importing lemma_connectors.core.auth loaded {pkg}.client"
        )


def test_importing_jira_client_does_not_load_other_connectors():
    """Importing one connector's client must not transitively load the
    other six connector clients."""
    import importlib

    importlib.import_module("lemma_connectors.jira.client")
    for pkg in _CONNECTOR_PACKAGES:
        if pkg == "jira":
            continue
        assert f"lemma_connectors.{pkg}.client" not in sys.modules, (
            f"Importing jira.client loaded {pkg}.client"
        )


def test_top_level_jira_client_lazy_resolves():
    """from lemma_connectors import JiraClient must still work via the
    PEP 562 __getattr__ lazy loader."""
    import importlib

    mod = importlib.import_module("lemma_connectors")
    jira_client_cls = mod.JiraClient
    assert jira_client_cls.__name__ == "JiraClient"
    assert "lemma_connectors.jira.client" in sys.modules


def test_gmail_client_construction_does_not_load_all_resources():
    """Constructing a GmailClient must not eagerly import every resource
    module — only the resources/__init__.py registry should be loaded."""
    import importlib

    from lemma_connectors.core.auth import OAuth2Credentials

    gmail_client_module = importlib.import_module("lemma_connectors.gmail.client")
    client = gmail_client_module.GmailClient(
        credentials=OAuth2Credentials(access_token="t", token_type="Bearer"),
    )
    resource_modules_loaded = [
        name
        for name in sys.modules
        if name.startswith("lemma_connectors.gmail.resources.")
        and not name.endswith("__init__")
    ]
    assert resource_modules_loaded == [], (
        f"Constructing GmailClient eagerly loaded: {resource_modules_loaded}"
    )


def test_get_operation_loads_only_target_resource():
    """get_operation('messages_send') must import only the messages resource
    module, not all 15 gmail resource modules."""
    import importlib

    from lemma_connectors.core.auth import OAuth2Credentials

    gmail_client_module = importlib.import_module("lemma_connectors.gmail.client")
    client = gmail_client_module.GmailClient(
        credentials=OAuth2Credentials(access_token="t", token_type="Bearer"),
    )

    before = set(
        name
        for name in sys.modules
        if name.startswith("lemma_connectors.gmail.resources.")
        and not name.endswith("__init__")
    )
    client.get_operation("messages_send")
    after = set(
        name
        for name in sys.modules
        if name.startswith("lemma_connectors.gmail.resources.")
        and not name.endswith("__init__")
    )
    newly_loaded = after - before
    assert newly_loaded == {"lemma_connectors.gmail.resources.messages"}, (
        f"Expected only messages resource, got: {newly_loaded}"
    )


def test_list_operation_names_requires_no_resource_imports():
    """list_operation_names() returns all operation names without importing
    any resource module."""
    import importlib

    from lemma_connectors.core.auth import OAuth2Credentials

    gmail_client_module = importlib.import_module("lemma_connectors.gmail.client")
    client = gmail_client_module.GmailClient(
        credentials=OAuth2Credentials(access_token="t", token_type="Bearer"),
    )
    names = client.list_operation_names()
    assert "messages_send" in names
    assert "labels_create" in names
    resource_modules = [
        name
        for name in sys.modules
        if name.startswith("lemma_connectors.gmail.resources.")
        and not name.endswith("__init__")
    ]
    assert resource_modules == [], (
        f"list_operation_names loaded resource modules: {resource_modules}"
    )


def test_get_operation_unknown_raises_operation_not_found():
    """get_operation for a non-existent operation must raise without
    loading any resource module."""
    from lemma_connectors.core.errors import OperationNotFoundError

    import importlib

    from lemma_connectors.core.auth import OAuth2Credentials

    gmail_client_module = importlib.import_module("lemma_connectors.gmail.client")
    client = gmail_client_module.GmailClient(
        credentials=OAuth2Credentials(access_token="t", token_type="Bearer"),
    )
    with pytest.raises(OperationNotFoundError):
        client.get_operation("nonexistent_operation")


def test_lazy_resource_namespace_attribute_access():
    """client.resources.messages must lazily build the resource on first
    access and cache it."""
    import importlib

    from lemma_connectors.core.auth import OAuth2Credentials

    gmail_client_module = importlib.import_module("lemma_connectors.gmail.client")
    client = gmail_client_module.GmailClient(
        credentials=OAuth2Credentials(access_token="t", token_type="Bearer"),
    )
    assert "lemma_connectors.gmail.resources.messages" not in sys.modules
    messages_resource = client.resources.messages
    assert "lemma_connectors.gmail.resources.messages" in sys.modules
    cached = client.resources.messages
    assert cached is messages_resource
