"""What a release's ``preview_url`` says when there is nowhere to preview it.

A release is previewed at its own host, ``<slug>--r<n>.<app_base_domain>``. Not
every stack serves one: Desktop and a tunnel put the workspace and the API on a
single origin, and ``app_base_domain`` is blank there — which is also the local
and testing default, so it is the common case rather than a broken install.

``preview_url`` was annotated ``str`` while ``public_app_url`` returned
``str | None``. Pydantic does not validate a computed field's return type, so
the field serialized ``null`` while the schema — and both generated SDKs, and
the client's own hand-written type — promised a string. The client believed it
held a URL, pointed its frame at nothing, fell back to the live release, and
announced it as a preview of an older one. The type is the fix; these pin it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.modules.apps.api.schemas.app_schemas import AppReleaseResponse

pytestmark = pytest.mark.unit


def _release() -> AppReleaseResponse:
    return AppReleaseResponse(
        id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        release_number=7,
        version="sha256:" + "a" * 64,
        created_at=datetime.now(timezone.utc),
        is_live=False,
        has_source=True,
        app_public_slug="orders",
    )


def test_preview_url_is_none_where_no_app_host_is_served(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "")
    assert _release().preview_url is None


def test_preview_url_serializes_as_null_rather_than_being_omitted(monkeypatch):
    # Present-and-null, not absent: the client distinguishes "no preview host"
    # from a field it failed to read, and the schema keeps it required.
    monkeypatch.setattr(settings, "app_base_domain", "")
    payload = json.loads(_release().model_dump_json())
    assert "preview_url" in payload
    assert payload["preview_url"] is None


def test_preview_url_names_the_release_host_when_one_is_served(monkeypatch):
    # The scheme is the API's, not a constant -- pinned here rather than read
    # from settings, so the assertion states the whole URL it expects.
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.com")
    monkeypatch.setattr(settings, "api_url", "https://api.example.com")
    assert _release().preview_url == "https://orders--r7.apps.example.com"


def test_schema_admits_null_so_generated_clients_do_too(monkeypatch):
    # The bug was not the value but the contract: the SDKs are generated from
    # this schema, so a `str` here reintroduces it in three languages at once.
    schema = AppReleaseResponse.model_json_schema(mode="serialization")
    assert schema["properties"]["preview_url"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
