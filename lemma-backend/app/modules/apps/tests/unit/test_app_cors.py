"""Unit tests for app-subdomain CORS allowance."""

from __future__ import annotations

import re

import app.core.cors as cors


def test_app_subdomain_regex_matches_slug_and_base(monkeypatch):
    monkeypatch.setattr(cors.settings, "app_base_domain", "apps.lemma.work")
    monkeypatch.setattr(cors.settings, "cors_origin_regex", None)

    pattern = cors.get_allowed_cors_origin_regex()
    assert pattern is not None
    assert re.fullmatch(pattern, "https://home-abc123.apps.lemma.work")
    assert re.fullmatch(pattern, "https://apps.lemma.work")
    assert not re.fullmatch(pattern, "https://apps.lemma.work.evil.com")
    assert not re.fullmatch(pattern, "https://evil.com")


def test_local_app_domain_with_port(monkeypatch):
    monkeypatch.setattr(cors.settings, "app_base_domain", "apps.lemma.localhost:8711")
    monkeypatch.setattr(cors.settings, "cors_origin_regex", None)

    pattern = cors.get_allowed_cors_origin_regex()
    assert re.fullmatch(pattern, "http://home-x.apps.lemma.localhost:8711")
    assert re.fullmatch(pattern, "http://apps.lemma.localhost:8711")


def test_combines_with_configured_regex(monkeypatch):
    monkeypatch.setattr(cors.settings, "app_base_domain", "apps.lemma.work")
    monkeypatch.setattr(cors.settings, "cors_origin_regex", r"https://.*\.example\.com")

    pattern = cors.get_allowed_cors_origin_regex()
    assert re.fullmatch(pattern, "https://foo.example.com")
    assert re.fullmatch(pattern, "https://home-1.apps.lemma.work")
