"""Tests for the daemon ``daemon.ready`` auto-bootstrap of PERSONAL runtime profiles.

When a user's daemon connects for the first time, the chat needs a
default runtime to pick. The gogett-hub project makes GG Coder that
default; the auto-bootstrap creates a PERSONAL USER_DAEMON profile
for every detected harness the user hasn't already added.

The function must be:
  - Idempotent on reconnect (no second profile created).
  - No-op if the user is in no orgs.
  - No-op if a harness is unavailable (``available: False``).
  - Tolerant of ``ValueError`` / ``RuntimeError`` from the service
    so a flaky reconnect doesn't surface an error to the daemon.

``uow_factory`` is captured as a closure variable in the websocket
route, not a module attribute, so we cannot ``monkeypatch.setattr``
it on the module — we have to inject the fake uow factory onto the
module (the helper reads the closure-captured name from the module
scope, and the test resets it on teardown so subsequent tests see
the real factory).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.modules.agent.api.controllers import runtime_config_controller as rcc
from app.modules.agent.api.controllers.runtime_config_controller import (
    _ensure_user_daemon_default_profile,
)
from app.modules.agent.domain.value_objects import HarnessKind


def _make_daemon() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        device_key="dev",
        display_name="Workstation",
        status="ONLINE",
        device_info={},
        harness_catalog={},
    )


class _FakeService:
    """Stub for ``AgentRuntimeProfileService``; the tests build one of
    these per scenario so the assertions can read the recorded calls.
    """

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.should_raise: BaseException | None = None

    async def create_user_daemon_profile(self, **kwargs):
        if self.should_raise is not None:
            raise self.should_raise
        self.created.append(kwargs)
        return SimpleNamespace(id=uuid4())


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Wire up the three external dependencies the helper needs.

    Returns a small handle the test body can read to drive the
    fake. ``monkeypatch`` undoes the identity-repo mutation; the
    module-level ``uow_factory`` and ``_runtime_profile_service``
    overrides are reset in the ``finally`` below so the websocket
    route's closure still works for any later tests.
    """
    fake_service = _FakeService()
    user_orgs: list = [SimpleNamespace(id=uuid4())]

    # Inject a module-level uow_factory that the helper's closure
    # picks up. Restore the original (no-op) attribute on teardown
    # so we don't poison other tests' calls to ``daemon_websocket``.
    original_uow_factory = getattr(rcc, "uow_factory", None)
    rcc.uow_factory = lambda: MagicMock()  # type: ignore[assignment]
    original_rps = rcc._runtime_profile_service
    rcc._runtime_profile_service = lambda uow: fake_service  # type: ignore[assignment]

    async def fake_get_user_organizations(self, *, user_id):  # noqa: ARG001
        # Production ``get_user_organizations`` returns ``(orgs, next_cursor)``;
        # the fake has to mirror that tuple shape, otherwise the production
        # call (which unpacks the result) would crash on ``user_orgs[0].id``.
        return list(user_orgs), None

    from app.modules.identity.infrastructure import organization_repositories

    monkeypatch.setattr(
        organization_repositories.OrganizationRepository,
        "get_user_organizations",
        fake_get_user_organizations,
    )

    class _Handle:
        service = property(lambda self: fake_service)
        user_orgs = property(lambda self: user_orgs)

        @staticmethod
        def set_user_orgs(orgs):
            user_orgs.clear()
            user_orgs.extend(orgs)

    try:
        yield _Handle()
    finally:
        rcc._runtime_profile_service = original_rps  # type: ignore[assignment]
        if original_uow_factory is None:
            try:
                del rcc.uow_factory  # type: ignore[attr-defined]
            except AttributeError:
                pass
        else:
            rcc.uow_factory = original_uow_factory  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_auto_bootstrap_creates_one_profile_per_harness(patched):
    daemon = _make_daemon()
    catalog = {
        HarnessKind.GG_CODER.value: {
            "available": True,
            "models": ["default"],
            "model_catalog": [
                {"name": "default", "display_name": "Default", "provider_model_name": "default"}
            ],
        },
        HarnessKind.CLAUDE_CODE.value: {
            "available": True,
            "models": ["sonnet"],
            "model_catalog": [],
        },
    }

    await _ensure_user_daemon_default_profile(
        user_id=daemon.user_id, daemon=daemon, harness_catalog=catalog
    )

    kinds = {kwargs["harness_kind"] for kwargs in patched.service.created}
    assert kinds == {HarnessKind.GG_CODER, HarnessKind.CLAUDE_CODE}
    for kwargs in patched.service.created:
        assert kwargs["scope"] is rcc.RuntimeProfileScope.PERSONAL
        assert kwargs["user_id"] == daemon.user_id
        assert kwargs["daemon_id"] == daemon.id
        # Names follow "<display> · <harness>"
        assert kwargs["name"].startswith("Workstation · ")


@pytest.mark.asyncio
async def test_auto_bootstrap_skips_unavailable_harnesses(patched):
    daemon = _make_daemon()
    catalog = {
        HarnessKind.GG_CODER.value: {
            "available": False,  # user hasn't installed ggcoder
            "models": [],
        },
        HarnessKind.CLAUDE_CODE.value: {
            "available": True,
            "models": ["sonnet"],
        },
    }

    await _ensure_user_daemon_default_profile(
        user_id=daemon.user_id, daemon=daemon, harness_catalog=catalog
    )

    # Only CLAUDE_CODE is created; GG_CODER is filtered by available:False.
    assert [c["harness_kind"] for c in patched.service.created] == [
        HarnessKind.CLAUDE_CODE
    ]


@pytest.mark.asyncio
async def test_auto_bootstrap_no_ops_when_user_has_no_orgs(patched):
    daemon = _make_daemon()
    catalog = {HarnessKind.GG_CODER.value: {"available": True, "models": []}}
    patched.set_user_orgs([])

    await _ensure_user_daemon_default_profile(
        user_id=daemon.user_id, daemon=daemon, harness_catalog=catalog
    )

    assert patched.service.created == []


@pytest.mark.asyncio
async def test_auto_bootstrap_swallows_value_error_on_reconnect(patched):
    """On reconnect the service raises ``ValueError`` (duplicate name).

    The helper must NOT propagate that to the daemon handshake;
    it should treat the second connect as a no-op so a flaky
    reconnect doesn't crash the daemon.
    """
    daemon = _make_daemon()
    catalog = {HarnessKind.GG_CODER.value: {"available": True, "models": []}}
    patched.service.should_raise = ValueError("profile with this name already exists")

    # Should NOT raise.
    await _ensure_user_daemon_default_profile(
        user_id=daemon.user_id, daemon=daemon, harness_catalog=catalog
    )


@pytest.mark.asyncio
async def test_auto_bootstrap_swallows_runtime_error_on_transient_daemon_issue(patched):
    """``RuntimeError`` from the service (e.g. daemon row deleted) is also a no-op."""
    daemon = _make_daemon()
    catalog = {HarnessKind.GG_CODER.value: {"available": True, "models": []}}
    patched.service.should_raise = RuntimeError("daemon row not found")

    await _ensure_user_daemon_default_profile(
        user_id=daemon.user_id, daemon=daemon, harness_catalog=catalog
    )
