from __future__ import annotations

import re
from urllib.parse import urlparse

from app.core.config import settings


def _normalise_origin(origin: str | None) -> str | None:
    if not origin:
        return None

    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.netloc:
        return None

    return f"{parsed.scheme}://{parsed.netloc}"


def get_allowed_cors_origins() -> list[str]:
    candidates = [
        settings.frontend_url,
        settings.auth_frontend_url,
        *settings.cors_origins,
    ]

    unique_origins: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        origin = _normalise_origin(candidate)
        if origin is None or origin in seen:
            continue
        seen.add(origin)
        unique_origins.append(origin)

    return unique_origins


def _app_subdomain_origin_regex() -> str | None:
    """Match any ``<slug>.<app_base_domain>`` (and the bare base) origin.

    No-build apps are served from per-slug subdomains and call the API with
    credentials, so each subdomain must be an allowed CORS origin. The base
    domain may carry a port locally (e.g. ``apps.lemma.localhost:8711``).
    """
    base_domain = (settings.app_base_domain or "").strip()
    if not base_domain:
        return None
    return rf"https?://([a-z0-9-]+\.)?{re.escape(base_domain)}"


def get_allowed_cors_origin_regex() -> str | None:
    patterns = [
        pattern
        for pattern in (settings.cors_origin_regex, _app_subdomain_origin_regex())
        if pattern
    ]
    if not patterns:
        return None
    if len(patterns) == 1:
        return patterns[0]
    return "|".join(f"(?:{pattern})" for pattern in patterns)
