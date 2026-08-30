"""Believing an inbound email's ``From:`` line, or not.

Every chat platform asserts its sender inside a payload we verified. Email
asserts nothing: ``From:`` is text the sender chose, and it resolves to the
Lemma user an agent run then executes as.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.email_authentication import (
    EmailAuthenticationVerdict,
    evaluate_email_authentication,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)

pytestmark = pytest.mark.unit


def _headers(*values: str) -> list[dict[str, str]]:
    return [{"name": "Authentication-Results", "value": value} for value in values]


def _verdict(*values: str, sender: str = "a@example.com", trusted=None):
    return evaluate_email_authentication(
        _headers(*values), from_address=sender, trusted_authserv_ids=trusted
    )


# --- reading the header ---------------------------------------------------


def test_dmarc_pass_is_believed() -> None:
    assert (
        _verdict("mx.google.com; dmarc=pass header.from=example.com")
        is EmailAuthenticationVerdict.PASS
    )


def test_dmarc_fail_is_not() -> None:
    assert (
        _verdict("mx.google.com; dmarc=fail header.from=example.com")
        is EmailAuthenticationVerdict.FAIL
    )


def test_no_header_at_all_is_unknown_not_failure() -> None:
    """A provider that adds no header is a deployment question, not an attack."""
    assert (
        evaluate_email_authentication([], from_address="a@example.com")
        is EmailAuthenticationVerdict.UNKNOWN
    )


def test_dkim_pass_aligned_to_the_from_domain_is_believed() -> None:
    assert (
        _verdict("mx; dkim=pass header.d=example.com")
        is EmailAuthenticationVerdict.PASS
    )


def test_dkim_pass_for_somebody_elses_domain_is_not() -> None:
    """An unaligned pass is worth nothing: a valid signature by a third party."""
    assert (
        _verdict("mx; dkim=pass header.d=bulk-mailer.net")
        is EmailAuthenticationVerdict.UNKNOWN
    )


def test_relaxed_alignment_accepts_a_subdomain_of_the_signing_domain() -> None:
    assert (
        _verdict("mx; dkim=pass header.d=example.com", sender="a@news.example.com")
        is EmailAuthenticationVerdict.PASS
    )


def test_spf_pass_is_read_from_the_envelope_sender() -> None:
    assert (
        _verdict("mx; spf=pass smtp.mailfrom=bounce@example.com")
        is EmailAuthenticationVerdict.PASS
    )


def test_an_address_inside_a_comment_is_not_mistaken_for_a_property() -> None:
    """Comments are free text, and this one names an attacker-chosen address."""
    assert (
        _verdict(
            "mx; spf=fail (mx: domain of evil@attacker.test does not designate "
            "1.2.3.4) smtp.mailfrom=evil@attacker.test"
        )
        is EmailAuthenticationVerdict.FAIL
    )


def test_only_the_receivers_own_header_is_read() -> None:
    """The attack this ordering exists for.

    Anyone can put Authentication-Results in the message they send. The
    receiver prepends its own, so the first is the real one -- and a map keyed
    by header name would be last-wins and let the forged copy decide.
    """
    assert (
        _verdict(
            "mx.google.com; dmarc=fail header.from=example.com",
            "attacker-supplied; dmarc=pass header.from=example.com",
        )
        is EmailAuthenticationVerdict.FAIL
    )


def test_a_named_trusted_receiver_ignores_every_other_header() -> None:
    trusted = frozenset({"mx.google.com"})
    assert (
        _verdict(
            "attacker-supplied; dmarc=pass header.from=example.com",
            "mx.google.com; dmarc=fail header.from=example.com",
            trusted=trusted,
        )
        is EmailAuthenticationVerdict.FAIL
    )


def test_headers_from_nobody_we_trust_are_as_good_as_absent() -> None:
    assert (
        _verdict(
            "attacker-supplied; dmarc=pass header.from=example.com",
            trusted=frozenset({"mx.google.com"}),
        )
        is EmailAuthenticationVerdict.UNKNOWN
    )


# --- what the gate does with it -------------------------------------------


def _event(
    verdict: str | None, *, platform: str = "RESEND"
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="thread-1",
        sender_external_user_id="a@example.com",
        sender_email="a@example.com",
        sender_authentication=verdict,
        message_text="hello",
    )


def _service(cached_user_id):
    """A resolver whose cache is already warm for this sender."""
    service = SurfaceIdentityResolutionService.__new__(SurfaceIdentityResolutionService)
    service._users = SimpleNamespace(
        get_id_by_email_insensitive=AsyncMock(return_value=cached_user_id),
        get_ids_by_mobile_numbers=AsyncMock(return_value=[]),
    )
    service.external_user_repository = SimpleNamespace()
    service._upsert = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            resolved_user_id=cached_user_id,
            external_user_id="a@example.com",
            email="a@example.com",
            phone=None,
            display_name=None,
        )
    )
    return service


async def test_a_failed_sender_never_resolves_even_when_already_cached() -> None:
    """The reason the check sits above the cache.

    On email the external_user_id *is* the From address, so a spoofed message
    from an address that resolved once before would take the cache-hit return
    and never reach a check placed with the other matches.
    """
    known = uuid4()
    resolved = await _service(known).resolve(event=_event("FAIL"))
    assert resolved.internal_user_id is None


async def test_an_authenticated_sender_resolves(monkeypatch) -> None:
    known = uuid4()
    resolved = await _service(known).resolve(event=_event("PASS"))
    assert resolved.internal_user_id == known


async def test_an_unauthenticated_sender_follows_the_deployment_setting(
    monkeypatch,
) -> None:
    from app.modules.agent_surfaces.config import surface_settings

    known = uuid4()
    monkeypatch.setattr(
        surface_settings, "surface_email_allow_unauthenticated_identity", False
    )
    assert (
        await _service(known).resolve(event=_event("UNKNOWN"))
    ).internal_user_id is None
    monkeypatch.setattr(
        surface_settings, "surface_email_allow_unauthenticated_identity", True
    )
    assert (
        await _service(known).resolve(event=_event("UNKNOWN"))
    ).internal_user_id == known


async def test_a_chat_platform_is_never_asked_to_authenticate() -> None:
    """Its sender came inside a payload whose signature was already verified.

    This test used to build a ``RESEND`` event while claiming to be about chat,
    so what it actually pinned was an *email* message with no verdict
    resolving — the bypass below, asserted as correct behaviour.
    """
    known = uuid4()
    resolved = await _service(known).resolve(event=_event(None, platform="SLACK"))
    assert resolved.internal_user_id == known


async def test_an_email_with_no_verdict_at_all_is_unknown_not_trusted(
    monkeypatch,
) -> None:
    """A missing verdict on email means the check did not run, not that it passed.

    Only ``merge_received_email`` ever sets one, so it is absent whenever
    enrichment did not: no ``email_id`` on the webhook, an ``HTTPError`` on the
    body fetch, or the whole polling receiver. Read as the chat platforms'
    "nothing to ask", an attacker who could make that fetch fail skipped
    authentication entirely, whatever the setting said.
    """
    from app.modules.agent_surfaces.config import surface_settings

    known = uuid4()
    monkeypatch.setattr(
        surface_settings, "surface_email_allow_unauthenticated_identity", False
    )
    assert (await _service(known).resolve(event=_event(None))).internal_user_id is None


async def test_an_email_with_no_verdict_still_follows_a_permissive_setting(
    monkeypatch,
) -> None:
    """It is UNKNOWN, so policy decides — the same as an unusable header."""
    from app.modules.agent_surfaces.config import surface_settings

    known = uuid4()
    monkeypatch.setattr(
        surface_settings, "surface_email_allow_unauthenticated_identity", True
    )
    assert (await _service(known).resolve(event=_event(None))).internal_user_id == known
