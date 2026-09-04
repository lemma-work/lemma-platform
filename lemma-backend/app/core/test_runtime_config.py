"""Unit tests for the shared browser runtime-config module."""

from __future__ import annotations

from uuid import uuid4

from app.core import app_install
from app.core.runtime_config import (
    build_runtime_config,
    inject_runtime_config,
    runtime_config_token,
)


def test_payload_has_pod_api_auth():
    pod_id = uuid4()
    cfg = build_runtime_config(pod_id)
    assert cfg["podId"] == str(pod_id)
    assert set(cfg) == {"podId", "apiUrl", "authUrl"}


def test_token_changes_with_pod():
    assert runtime_config_token(uuid4()) != runtime_config_token(uuid4())


def test_token_changes_with_app_identity():
    pod_id = uuid4()
    assert runtime_config_token(pod_id, app={"name": "One"}) != runtime_config_token(
        pod_id, app={"name": "Two"}
    )


def test_injects_after_head_and_is_idempotent():
    pod_id = uuid4()
    html = b"<html><head><meta></head><body>x</body></html>"
    out = inject_runtime_config(html, pod_id).decode()
    assert "data-lemma-runtime-config" in out
    assert str(pod_id) in out
    assert (
        out.index("<head") < out.index("data-lemma-runtime-config") < out.index("<body")
    )
    # idempotent on the sentinel
    assert (
        inject_runtime_config(out.encode(), pod_id)
        .decode()
        .count("data-lemma-runtime-config")
        == 1
    )


def test_injects_at_top_when_no_head():
    out = inject_runtime_config(b"<div>x</div>", uuid4()).decode()
    assert out.startswith("<script data-lemma-runtime-config")


def test_config_values_are_escaped():
    # JSON payload is <-escaped so it cannot break out of the script element.
    out = inject_runtime_config(b"<head></head>", uuid4()).decode()
    assert "</script>" in out  # only our own closing tag
    assert out.count("<script data-lemma-runtime-config>") == 1


def test_injects_sanitized_app_identity():
    out = inject_runtime_config(
        b"<head></head>",
        uuid4(),
        app={
            "name": "Support Triage",
            "description": "Route urgent work",
            "private": "not-exposed",
        },
    ).decode()
    assert (
        '"app": {"name": "Support Triage", "description": "Route urgent work"}' in out
    )
    assert "not-exposed" not in out


def _public_app() -> dict[str, str]:
    return {
        "name": "Invoice Tracker",
        "description": "Track invoices",
        "url": "https://invoice-tracker.apps.lemma.work",
    }


def test_public_app_entrypoint_is_installable():
    out = inject_runtime_config(
        b"<html><head></head><body>x</body></html>", uuid4(), app=_public_app()
    ).decode()

    assert f'href="{app_install.MANIFEST_PATH}"' in out
    assert 'rel="apple-touch-icon"' in out
    assert app_install.APP_INSTALL_SENTINEL in out
    assert out.index("<head") < out.index('rel="manifest"') < out.index("<body")


def test_a_widget_is_not_offered_an_install():
    # A widget is served from the API host, so it has no origin of its own for
    # a manifest to scope -- offering the install would install the API.
    out = inject_runtime_config(
        b"<html><head></head><body>x</body></html>", uuid4()
    ).decode()

    assert "manifest" not in out
    assert app_install.APP_INSTALL_SENTINEL not in out


def test_install_injection_is_idempotent():
    once = inject_runtime_config(
        b"<html><head></head><body>x</body></html>", uuid4(), app=_public_app()
    )
    twice = inject_runtime_config(once, uuid4(), app=_public_app()).decode()

    assert twice.count(app_install.APP_INSTALL_SENTINEL) == 1


def test_token_covers_the_install_script():
    # The script is host-authored, so nothing else in the tag moves when it
    # changes -- and a no-cache entrypoint would keep answering 304 with the
    # old one baked in.
    pod_id = uuid4()
    assert runtime_config_token(pod_id, app=_public_app()) != runtime_config_token(
        pod_id, app={"name": "Invoice Tracker", "description": "Track invoices"}
    )
