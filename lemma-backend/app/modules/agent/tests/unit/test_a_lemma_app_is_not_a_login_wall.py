"""Asking a person to sign in to a Lemma app is a loop with no exit.

Observed on a live stack: the agent got a saved login for `lemma.work`, which
worked, then opened `factory-ledger.apps.lemma.work` and hit "Login with
Lemma". That button redirects to the website, which *is* signed in, and comes
back no better off, because the session cookies are host-only on the website
and API hosts and the browser sends none of them to an app host. The app's SDK
then falls back to a cookie check, finds nothing, and offers to log in again.

What an app actually reads is a token in its own `localStorage`, which
`lemma apps open` seeds. So the tool refuses rather than pausing a person in
front of a login screen that cannot ever satisfy them.
"""

from __future__ import annotations

import pytest

from app.modules.agent.tools.browser.sign_in import _pod_app_slug

pytestmark = pytest.mark.unit


def test_an_app_host_is_recognised(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_base_domain", "apps.lemma.work")

    assert _pod_app_slug("https://factory-ledger.apps.lemma.work") == "factory-ledger"
    # The website and the API are not apps, and must still be askable.
    assert _pod_app_slug("https://lemma.work") is None
    assert _pod_app_slug("https://api.lemma.work") is None
    # The base domain on its own is not an app either -- a slug of "" would be
    # worse advice than none.
    assert _pod_app_slug("https://apps.lemma.work") is None
    # A site that merely ends in a similar string is not ours.
    assert _pod_app_slug("https://evil-apps.lemma.work.attacker.test") is None


def test_the_port_travels_with_a_local_base_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local development serves apps on a port, and it is part of the host."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_base_domain", "apps.lemma.localhost:8710")

    # The port is not part of the host `host_of` reports, so it is stripped
    # from the setting before comparing -- otherwise no local app ever matches.
    assert _pod_app_slug("http://shop.apps.lemma.localhost:8710") == "shop"
    assert _pod_app_slug("http://shop.apps.lemma.localhost") == "shop"


def test_nothing_is_an_app_when_no_base_domain_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that serves no apps must not have ordinary sites refused."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_base_domain", "")

    assert _pod_app_slug("https://factory-ledger.apps.lemma.work") is None
