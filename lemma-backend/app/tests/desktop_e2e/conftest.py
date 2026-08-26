"""Standing up a real Lemma Desktop install and driving it like a user.

Everything here talks to an install over HTTP. Nothing is mocked, faked, or
monkeypatched -- the point of this suite is the paths that differ between the
desktop build and the server build (the host pack's environment, the VZ guest,
locald's ports, WKWebView), and every one of those is invisible to a test that
substitutes anything.

Opt in with ``LEMMA_DESKTOP_E2E=1``. Addresses come from the install itself
(``locald/network.json``) rather than being guessed, so a run cannot silently
target the wrong thing.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

# Where a packaged install keeps its state. The override exists so the harness
# can point at a throwaway root instead of the developer's real install.
DEFAULT_LOCALD_ROOT = (
    Path.home() / "Library" / "Application Support" / "Lemma" / "locald"
)


def locald_root() -> Path:
    override = os.getenv("LEMMA_DESKTOP_E2E_LOCALD_ROOT")
    return Path(override) if override else DEFAULT_LOCALD_ROOT


@dataclass(frozen=True)
class Install:
    """The addresses of a running install, read from what it actually wrote."""

    api_url: str
    frontend_url: str
    app_base_domain: str
    # A throwaway stack borrows the guest but runs no locald, so nothing
    # dispatches functions into a sandbox. A packaged install does.
    provisions_sandboxes: bool = True

    def app_url(self, slug: str) -> str:
        return f"http://{slug}.{self.app_base_domain}/"


def _read_throwaway_stack() -> Install | None:
    """A stack stood up by ``desktop/e2e/throwaway_stack.py``, if there is one.

    Preferred over a packaged install when present, because that is the lane
    where a change in the working tree can be seen working *before* it becomes
    a DMG. It records its own addresses, including the fact that it runs no
    separate frontend.
    """
    stack_file = locald_root().parent / "stack.json"
    if not stack_file.is_file():
        return None
    try:
        stack = json.loads(stack_file.read_text())
        return Install(
            api_url=stack["api_url"],
            frontend_url=stack["frontend_url"],
            app_base_domain=stack["app_base_domain"],
            provisions_sandboxes=bool(stack.get("provisions_sandboxes", True)),
        )
    except OSError, json.JSONDecodeError, KeyError:
        return None


def _read_install() -> Install | None:
    """Read the addresses out of the install, or None if it is not there.

    ``network.json`` is the ports locald allocated; ``host-pack.json`` is the
    environment it rendered for the backend. Both are read rather than one
    inferred from the other, because ``APP_BASE_DOMAIN`` carries the port and
    getting that wrong produces a 404 that looks like a missing app.
    """
    throwaway = _read_throwaway_stack()
    if throwaway is not None:
        return throwaway

    root = locald_root()
    try:
        ports = json.loads((root / "network.json").read_text())
        pack = json.loads((root / "host-pack.json").read_text())
    except OSError, json.JSONDecodeError:
        return None

    app_base = None
    for service in pack.get("services", []):
        env = service.get("env") or {}
        if "APP_BASE_DOMAIN" in env:
            app_base = env["APP_BASE_DOMAIN"]
            break
    if not app_base:
        return None

    backend = ports.get("backend_port")
    frontend = ports.get("frontend_port")
    if not backend or not frontend:
        return None
    return Install(
        api_url=f"http://app.lemma.localhost:{backend}",
        frontend_url=f"http://app.lemma.localhost:{frontend}",
        app_base_domain=app_base,
    )


def _requirements() -> list[str]:
    """Everything missing that this suite cannot supply for itself.

    Reported together rather than one failure at a time: standing this up has
    several steps, and finding out about them one run apiece is what stops
    people running it at all. (Same reasoning, and same shape, as
    ``test_lemma_local_real_guest.py``.)
    """
    missing: list[str] = []
    if os.getenv("LEMMA_DESKTOP_E2E") != "1":
        missing.append("LEMMA_DESKTOP_E2E=1 to opt in")

    install = _read_install()
    if install is None:
        missing.append(
            f"no running install under {locald_root()} — start Lemma Desktop, "
            "or set LEMMA_DESKTOP_E2E_LOCALD_ROOT"
        )
        return missing

    try:
        response = httpx.get(f"{install.api_url}/health/ready", timeout=10)
        if response.status_code != 200:
            missing.append(
                f"{install.api_url}/health/ready answered {response.status_code}"
            )
        elif response.json().get("status") != "ready":
            missing.append(f"the backend is not ready: {response.text[:120]}")
    except httpx.HTTPError as error:
        missing.append(f"the backend at {install.api_url} is unreachable: {error}")
    return missing


@pytest.fixture(scope="session")
def install() -> Install:
    missing = _requirements()
    if missing:
        # Asking for the real thing and not getting it is a failure, not a skip.
        # A skip here is indistinguishable from a pass in CI output, and this
        # suite exists precisely because nothing else covers these paths.
        reason = "desktop install unavailable — " + "; ".join(missing)
        if os.getenv("LEMMA_DESKTOP_E2E") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    resolved = _read_install()
    assert resolved is not None
    return resolved


@dataclass(frozen=True)
class Account:
    email: str
    password: str
    access_token: str
    user_id: str


@pytest.fixture(scope="session")
def account(install: Install) -> Account:
    """A brand-new user, signed up through the install's own auth.

    Header token transfer rather than cookies: this fixture drives the API, and
    a Bearer token is what the Python SDK takes. The *browser* half of the
    session -- the part that actually broke -- is exercised in the WKWebView
    probe, which signs in the way a person does.

    A fresh account per run, because these tests create pods and apps and a
    shared one accumulates them.
    """
    # example.com, not a .test/.local address: the install validates the
    # domain on sign-up and rejects reserved TLDs outright.
    email = f"lemma-desktop-e2e-{secrets.token_hex(6)}@example.com"
    # Built to satisfy the password policy rather than hoping a random string
    # does. `token_urlsafe` produces one with no digit often enough that this
    # suite failed intermittently on "Password must contain at least one
    # number" -- a flake in the harness reads as a flake in the product.
    password = f"Pw1-{secrets.token_urlsafe(18)}-{secrets.randbelow(10)}"
    response = httpx.post(
        f"{install.api_url}/st/auth/signup",
        headers={"st-auth-mode": "header", "rid": "emailpassword"},
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": password},
            ]
        },
        timeout=60,
    )
    if response.status_code != 200 or response.json().get("status") != "OK":
        pytest.fail(
            f"could not sign up on the install: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
    token = response.headers.get("st-access-token")
    if not token:
        pytest.fail(
            "sign-up succeeded but returned no st-access-token header, so the "
            "install is not honouring header token transfer"
        )
    return Account(
        email=email,
        password=password,
        access_token=token,
        user_id=response.json()["user"]["id"],
    )
