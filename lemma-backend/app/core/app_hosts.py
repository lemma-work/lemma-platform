"""Which pod app a host name belongs to: ``<label>.<app_base_domain>``.

In core rather than in the apps module, because two layers ask it. Host routing
(``apps/api/host_routing.py``) serves an app's build by its host, and the
authentication gate (``core/app_pin.py``) pins a request from an app's host to
that app's pod. One definition, so the host an app is served from and the host
it is recognised by cannot drift.
"""

from __future__ import annotations

from app.core.config import settings


def split_release_label(label: str) -> tuple[str | None, str | None]:
    """Split an app host label into ``(slug, release_ref)``.

    ``orders`` serves whatever is live; ``orders--r7`` previews release 7 of
    ``orders``. ``--`` is unambiguous as the separator because
    ``normalize_public_slug`` collapses runs of ``-``, so a stored slug never
    contains one -- splitting on the LAST ``--`` recovers the slug exactly,
    however many single hyphens it has.

    Shared by the host middleware and the public controller on purpose: the
    middleware hands the controller the WHOLE label, and one function means the
    two cannot disagree about where a label divides.
    """
    if not label or "--" not in label:
        return (label or None), None
    slug, _, release_ref = label.rpartition("--")
    # A label that is all separator ("--r7", "orders--") names no app or no
    # release; treat it as unroutable rather than guessing which half was meant.
    if not slug or not release_ref:
        return None, None
    return slug, release_ref


def app_label_from_host(host: str) -> str | None:
    """Return the routable app label in ``host`` (``orders`` or ``orders--r7``).

    ``host`` may include a port. The label is the single left-most one in front
    of the configured ``app_base_domain``; the bare base domain (the main API
    host) and multi-level hosts are not apps. A label that names no app or no
    release (``--r7``, ``orders--``) is unroutable rather than a guess.
    """
    base = settings.app_base_domain
    if not base:
        return None
    host_no_port = host.split(":", 1)[0].strip().lower()
    base_no_port = base.split(":", 1)[0].strip().lower()
    if not host_no_port or not base_no_port:
        return None
    suffix = f".{base_no_port}"
    if not host_no_port.endswith(suffix):
        return None
    label = host_no_port[: -len(suffix)]
    if not label or "." in label:
        return None
    slug, _release = split_release_label(label)
    return label if slug else None


def app_slug_from_host(host: str) -> tuple[str | None, str | None]:
    """``(slug, release_ref)`` for ``host``, or ``(None, None)``.

    The middleware routes on the whole label; this is the split form, kept for
    callers that want the two halves separately.
    """
    label = app_label_from_host(host)
    return split_release_label(label) if label else (None, None)


__all__ = ["app_label_from_host", "app_slug_from_host", "split_release_label"]
