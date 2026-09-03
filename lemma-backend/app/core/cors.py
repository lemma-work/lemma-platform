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


def _configured_origins() -> list[str]:
    """``CORS_ORIGINS``, minus the loopback defaults outside local mode.

    The default list is eight loopback origins plus the Tauri schemes, and the
    middleware runs with ``allow_credentials=True``. It is right for a checkout,
    ``make dev`` and the desktop build, and it is a hole everywhere else: a
    deployment that sets ``FRONTEND_URL`` and ``API_URL`` -- what the
    configuration guide's URL block shows -- and never thinks about
    ``CORS_ORIGINS`` shipped with ``http://localhost:3000`` allowed to make
    credentialed calls and read the answers.

    Dropped only when the value is the *default*. An operator who names loopback
    origins has decided something; what is removed here is the decision nobody
    made. ``model_fields_set`` is what tells those apart, and it is why this is
    not simply a narrower default: the default has to stay usable locally.
    """
    origins = list(settings.cors_origins)
    if settings.is_local_mode() or "cors_origins" in settings.model_fields_set:
        return origins
    return [origin for origin in origins if not _is_loopback_default(origin)]


def _is_loopback_default(origin: str) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme in {"tauri", "http+tauri"}:
        return True
    host = parsed.hostname or ""
    return host in {"localhost", "127.0.0.1", "::1", "tauri.localhost"}


def get_allowed_cors_origins() -> list[str]:
    candidates = [
        settings.frontend_url,
        settings.auth_frontend_url,
        *_configured_origins(),
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

    ``http`` is allowed only where there is no TLS to insist on: local mode, or
    a base domain under the reserved ``.localhost`` loopback name, which a
    hosted deployment cannot have. Elsewhere a plain-HTTP page on the apps
    domain would be a credentialed origin, which hands an active network
    attacker a same-site position they would otherwise have to break TLS for.
    """
    base_domain = (settings.app_base_domain or "").strip()
    if not base_domain:
        return None
    host = base_domain.split(":", 1)[0]
    loopback = host == "localhost" or host.endswith(".localhost")
    scheme = "https?" if (settings.is_local_mode() or loopback) else "https"
    return rf"{scheme}://([a-z0-9-]+\.)?{re.escape(base_domain)}"


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
