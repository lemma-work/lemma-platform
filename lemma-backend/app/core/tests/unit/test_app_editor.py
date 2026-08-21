"""Unit tests for the app editor bridge and who is allowed to drive it."""

from __future__ import annotations

import json
import re
from html import unescape
from uuid import uuid4

import pytest

from app.core import app_editor
from app.core.app_editor import (
    EDITOR_BRIDGE_SENTINEL,
    editor_bridge_fingerprint,
    editor_bridge_script,
    editor_origins,
)
from app.core.runtime_config import inject_runtime_config, runtime_config_token

_HTML = b"<html><head><meta></head><body><div id='root'></div></body></html>"


def _allowlist_from(script: str) -> list[str]:
    match = re.search(r'data-lemma-editor-origins="([^"]*)"', script)
    assert match, "bridge script carries no origin allowlist"
    return json.loads(unescape(match.group(1)))


@pytest.fixture
def origins(monkeypatch):
    def _set(frontend_url: str, cors: list[str]):
        monkeypatch.setattr(app_editor.settings, "frontend_url", frontend_url)
        monkeypatch.setattr(app_editor.settings, "cors_origins", cors)

    return _set


def test_origins_include_frontend_and_cors(origins):
    origins("https://app.lemma.work/some/path", ["tauri://localhost"])
    assert editor_origins() == ["https://app.lemma.work", "tauri://localhost"]


def test_origins_drop_wildcard_and_duplicates(origins):
    origins("https://app.lemma.work", ["*", "https://app.lemma.work", "  "])
    assert editor_origins() == ["https://app.lemma.work"]


def test_bridge_is_not_served_when_no_origin_may_drive_it(origins):
    origins("", ["*"])
    assert editor_bridge_script() == ""


def test_bridge_script_carries_its_allowlist(origins):
    origins("https://app.lemma.work", ["tauri://localhost"])
    script = editor_bridge_script()
    assert _allowlist_from(script) == ["https://app.lemma.work", "tauri://localhost"]
    assert "lemma-app-editor:" in script


def test_bridge_body_cannot_close_the_script_element():
    # The element is only safe while nothing in the body reads as a closing tag.
    assert "</script" not in editor_bridge_script().removesuffix("</script>")


def test_app_entrypoint_carries_the_bridge():
    out = inject_runtime_config(
        _HTML, uuid4(), app={"name": "orders"}, app_id=uuid4()
    ).decode()
    assert EDITOR_BRIDGE_SENTINEL in out
    assert "__LEMMA_CONFIG__" in out


def test_widget_document_does_not_carry_the_bridge():
    # A widget has no source tree of its own to point an edit at.
    assert EDITOR_BRIDGE_SENTINEL not in inject_runtime_config(_HTML, uuid4()).decode()


def test_bridge_injection_is_idempotent():
    pod_id, app_id = uuid4(), uuid4()
    once = inject_runtime_config(_HTML, pod_id, app_id=app_id).decode()
    twice = inject_runtime_config(once, pod_id, app_id=app_id).decode()
    assert twice.count(EDITOR_BRIDGE_SENTINEL) == 1


def test_entrypoint_token_changes_with_the_allowlist(origins):
    pod_id = uuid4()
    origins("https://app.lemma.work", [])
    before = runtime_config_token(pod_id, app={"name": "orders"})
    origins("https://app.lemma.work", ["https://evil.example"])
    assert runtime_config_token(pod_id, app={"name": "orders"}) != before


def test_fingerprint_is_stable_for_unchanged_input(origins):
    origins("https://app.lemma.work", [])
    assert editor_bridge_fingerprint() == editor_bridge_fingerprint()
