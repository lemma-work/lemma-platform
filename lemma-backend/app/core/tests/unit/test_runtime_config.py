from uuid import uuid4

from app.core.runtime_config import (
    APP_BRANDING_SENTINEL,
    RUNTIME_CONFIG_SENTINEL,
    SOCIAL_METADATA_SENTINEL,
    build_app_branding,
    inject_runtime_config,
)
from app.core.config import settings


def test_public_app_runtime_includes_safe_social_metadata():
    body = inject_runtime_config(
        "<html><head><title>Desk</title></head><body></body></html>",
        uuid4(),
        app={
            "name": 'Research "Desk"',
            "description": "Evidence < guesses",
            "url": "https://research.apps.lemma.work",
        },
    ).decode()

    assert RUNTIME_CONFIG_SENTINEL in body
    assert SOCIAL_METADATA_SENTINEL in body
    assert 'property="og:title" content="Research &quot;Desk&quot;"' in body
    assert 'content="Evidence &lt; guesses"' in body
    assert 'name="twitter:card" content="summary_large_image"' in body
    assert 'rel="canonical" href="https://research.apps.lemma.work"' in body
    assert "https://lemma.work/api/social-card?" in body


def test_private_app_runtime_does_not_claim_a_public_url():
    body = inject_runtime_config(
        "<html><head></head><body></body></html>",
        uuid4(),
        app={"name": "Private desk"},
    ).decode()

    assert RUNTIME_CONFIG_SENTINEL in body
    assert SOCIAL_METADATA_SENTINEL not in body
    assert 'property="og:title"' not in body


def test_runtime_and_social_injection_are_idempotent():
    pod_id = uuid4()
    app = {
        "name": "Research Desk",
        "url": "https://research.apps.lemma.work",
    }
    branding = {
        "label": "Remix on Lemma",
        "url": "https://lemma.work/remix?source=research",
    }
    once = inject_runtime_config(
        "<html><head></head></html>",
        pod_id,
        app=app,
        branding=branding,
    )
    twice = inject_runtime_config(once, pod_id, app=app, branding=branding).decode()

    assert twice.count(RUNTIME_CONFIG_SENTINEL) == 1
    assert twice.count(SOCIAL_METADATA_SENTINEL) == 1
    assert twice.count(APP_BRANDING_SENTINEL) == 1


def test_public_app_branding_links_to_remix_handoff(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://lemma.work")
    public_url = "https://research.apps.lemma.work"
    branding = build_app_branding(public_url)

    body = inject_runtime_config(
        "<html><head></head><body></body></html>",
        uuid4(),
        app={"name": "Research Desk", "url": public_url},
        branding=branding,
    ).decode()

    assert APP_BRANDING_SENTINEL in body
    assert "data-lemma-branding-host" in body
    assert "Remix on Lemma" in body
    assert branding["url"] in body
    assert "source=https%3A%2F%2Fresearch.apps.lemma.work" in body


def test_public_app_branding_is_dismissable_and_persists_via_local_storage():
    branding = {
        "label": "Remix on Lemma",
        "url": "https://lemma.work/remix?source=research",
    }
    body = inject_runtime_config(
        "<html><head></head><body></body></html>",
        uuid4(),
        branding=branding,
    ).decode()

    assert "lemma:app-branding:dismissed" in body
    assert "localStorage.getItem(dismissKey)" in body
    assert "localStorage.setItem(dismissKey,'1')" in body
    assert 'aria-label="Dismiss"' in body
