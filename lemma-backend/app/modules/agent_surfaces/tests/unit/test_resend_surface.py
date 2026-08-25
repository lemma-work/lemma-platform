from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.platforms.resend.inbound import (
    normalize_resend_inbound as _normalize_resend_inbound,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfaceEventMode,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.resend.parser import (
    ResendInboundParser,
    merge_received_email,
)
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService
from app.modules.agent_surfaces.services.surface_service import AgentSurfaceService


def test_resend_is_email_and_default_webhook_binding():
    assert SurfacePlatform.RESEND.is_email is True
    # RESEND is system-credentialed email over a native webhook (no account_id).
    surface = AgentSurfaceEntity.create(
        pod_id=uuid4(),
        surface_type=SurfacePlatform.RESEND,
        agent_id=None,
        config=SurfaceConfig(),
        credential_mode=SurfaceCredentialMode.SYSTEM,
        account_id=None,
    )
    assert surface.mode is SurfaceMode.EMAIL
    assert surface.event_mode is SurfaceEventMode.WEBHOOK


def test_resend_inbound_parser_threads_and_builds_reply_target():
    parser = ResendInboundParser()
    event = parser.parse(
        {
            "from": "alice@example.com",
            "to": "pod-abc@ops.asur.work",
            "subject": "Re: Question",
            "text": "Here is my answer",
            "message_id": "<m2@example.com>",
            "in_reply_to": "<m1@example.com>",
            "references": ["<root@example.com>", "<m1@example.com>"],
        }
    )
    assert event is not None
    assert event.platform is SurfacePlatform.RESEND
    assert event.sender_email == "alice@example.com"
    # Thread groups by the references root.
    assert event.external_thread_id == "<root@example.com>"
    assert event.reply_target["recipient_email"] == "alice@example.com"
    # Outbound references chain = inbound references + this message id.
    assert event.reply_target["references"][-1] == "<m2@example.com>"
    assert event.metadata["surface_address"] == "pod-abc@ops.asur.work"


@pytest.mark.asyncio
async def test_resend_send_email_builds_resend_api_payload():
    service = ResendPlatformService(
        {
            "api_key": "re_test",
            "from_address": "pod-1@ops.asur.work",
            "from_name": "Lemma",
        }
    )
    captured = {}

    async def _fake_post(self, url, json, headers):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class _Resp:
            content = b"{}"

            def raise_for_status(self):
                return None

            def json(self):
                return {"id": "email-1"}

        return _Resp()

    with patch("httpx.AsyncClient.post", new=_fake_post):
        result = await service._send_email(
            recipient_email="bob@example.com",
            subject="Hello",
            in_reply_to="<m1@example.com>",
            references=["<m1@example.com>"],
            content="**hi** there",
            content_type="markdown",
            attachments=[("note.txt", b"data", "text/plain")],
        )

    assert result == {"id": "email-1"}
    assert captured["url"].endswith("/emails")
    assert captured["headers"]["Authorization"] == "Bearer re_test"
    body = captured["json"]
    assert body["from"] == "Lemma <pod-1@ops.asur.work>"
    assert body["to"] == ["bob@example.com"]
    # Bold survives; the tag now carries inline styling, because a mail client
    # cannot be relied on for a stylesheet. See email_styles.
    assert "<strong style=" in body["html"] and ">hi</strong>" in body["html"]
    assert body["headers"]["In-Reply-To"] == "<m1@example.com>"
    assert body["attachments"][0]["filename"] == "note.txt"


async def test_a_resend_surface_without_an_address_is_refused(monkeypatch):
    """The `pod-<hex>@` fallback is gone, and its absence is the point.

    It existed so a caller need not know how addresses are allocated, and two of
    the three callers duly did not: the surfaces API and the bundle applier both
    landed on it, so whether a person got `ops.acme@` or `pod-9f3c1e…@` came down
    to which door their surface arrived through. Neither of those paths was
    screened against `RESERVED_LOCAL_PARTS` either. Refusing is what keeps a
    fourth caller from re-opening that quietly.
    """
    from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError

    repo = AsyncMock()
    repo.get_by_pod_and_name.return_value = None
    binder = AsyncMock()
    binder.resolve_binding.return_value = (None, None, None)
    service = AgentSurfaceService(
        surface_repository=repo, account_binding_resolver=binder
    )

    with pytest.raises(AgentSurfaceValidationError, match="needs an inbound address"):
        await service.create_surface(
            pod_id=uuid4(),
            agent_id=None,
            platform=SurfacePlatform.RESEND,
            name="resend",
            config=SurfaceConfig(),
            credential_mode=SurfaceCredentialMode.SYSTEM,
        )

    repo.create.assert_not_awaited()


async def test_minting_without_a_domain_says_so_instead_of_inventing_one(monkeypatch):
    """A default domain is worse than an error.

    ``ops.asur.work`` used to be the fallback, so an unconfigured deployment
    silently minted addresses on a domain it does not own: outbound bounced and
    replies matched no surface, with nothing anywhere saying why.
    """
    from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
    from app.modules.agent_surfaces.services import email_surface_provisioning
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_inbound_domain", None)

    with pytest.raises(AgentSurfaceValidationError, match="RESEND_INBOUND_DOMAIN"):
        await email_surface_provisioning.create_surface_on_minted_address(
            AsyncMock(),
            AsyncMock(),
            pod_id=uuid4(),
            agent_id=None,
            agent_name=None,
            platform=SurfacePlatform.RESEND,
            name="inbox",
            config=SurfaceConfig(),
            credential_mode=SurfaceCredentialMode.SYSTEM,
        )


async def test_a_connected_account_does_not_need_the_deployment_s_key(monkeypatch):
    """Minting an address is not a question about whose credentials send it.

    A Resend surface can authenticate with a connected account, and one did:
    `test_resend_webhook_routes_raw_envelope_to_provisioned_address` sets the
    inbound domain and no system key. Gating this path on `email_is_configured`
    — key *and* domain, which is the right test for a SYSTEM mailbox — turned
    that into a refusal. Somewhere to mint the address is all this needs.
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.services import email_surface_provisioning
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")
    monkeypatch.setattr(core_settings, "resend_api_key", None)
    monkeypatch.setattr(
        email_surface_provisioning, "pod_name_for", AsyncMock(return_value="Acme")
    )
    assert not email_surface_provisioning.email_is_configured()

    service = AsyncMock()
    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=AsyncMock())

    await email_surface_provisioning.create_surface_on_minted_address(
        service,
        SimpleNamespace(session=session),
        pod_id=uuid4(),
        agent_id=uuid4(),
        agent_name="Ops",
        platform=SurfacePlatform.RESEND,
        name="inbox",
        config=SurfaceConfig(),
        credential_mode=SurfaceCredentialMode.CUSTOM,
        account_id=uuid4(),
    )

    address = service.create_surface.await_args.kwargs["surface_identity_email"]
    assert address == "ops.acme@ops.asur.work"


async def test_an_unnamed_second_mailbox_does_not_collide_with_the_pod_s(monkeypatch):
    """Every pod's assistant holds the surface named "resend" from creation.

    `create_surface` defaults an unnamed surface to its platform, so connecting
    email for an agent — no name given, which is what the UI sends — would come
    back "already exists". The agent-derived name is what the eager and lazy
    provisioning paths already pick.
    """
    from app.modules.agent_surfaces.services import email_surface_provisioning
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")
    monkeypatch.setattr(
        email_surface_provisioning, "pod_name_for", AsyncMock(return_value="Acme")
    )
    service = AsyncMock()
    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=AsyncMock())

    await email_surface_provisioning.create_surface_on_minted_address(
        service,
        SimpleNamespace(session=session),
        pod_id=uuid4(),
        agent_id=uuid4(),
        agent_name="Ops Assistant",
        platform=SurfacePlatform.RESEND,
        name=None,
        config=SurfaceConfig(),
        credential_mode=SurfaceCredentialMode.SYSTEM,
    )

    assert service.create_surface.await_args.kwargs["name"] == "resend-ops-assistant"


async def test_a_non_email_surface_passes_straight_through(monkeypatch):
    """The helper is a substitute for `create_surface`, not a special case.

    Both callers create every platform through it, so Slack and Telegram must
    not acquire an inbound address or a domain requirement on the way past.
    """
    from app.modules.agent_surfaces.services import email_surface_provisioning
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_inbound_domain", None)
    service = AsyncMock()

    await email_surface_provisioning.create_surface_on_minted_address(
        service,
        AsyncMock(),
        pod_id=uuid4(),
        agent_id=None,
        agent_name=None,
        platform=SurfacePlatform.SLACK,
        name="slack",
        config=SurfaceConfig(),
        credential_mode=SurfaceCredentialMode.SYSTEM,
    )

    assert "surface_identity_email" not in service.create_surface.await_args.kwargs


def test_normalize_resend_inbound_handles_envelope_and_shapes():
    normalized = _normalize_resend_inbound(
        {
            "type": "email.received",
            "data": {
                "from": {"address": "alice@example.com", "name": "Alice"},
                "to": [{"address": "pod-1@ops.asur.work"}],
                "subject": "Hi",
                "text": "body",
                "headers": [
                    {"name": "Message-ID", "value": "<m9@example.com>"},
                    {
                        "name": "References",
                        "value": "<r1@example.com> <r2@example.com>",
                    },
                ],
            },
        }
    )
    assert normalized["from"] == "alice@example.com"
    assert normalized["from_name"] == "Alice"
    assert normalized["to"] == "pod-1@ops.asur.work"
    assert normalized["message_id"] == "<m9@example.com>"
    assert normalized["references"] == ["<r1@example.com>", "<r2@example.com>"]


@pytest.mark.asyncio
async def test_a_cold_email_seeds_the_thread_and_is_not_a_reply():
    """First contact must be recognisable when they answer, and not say "Re:".

    Two claims in one send, because they fail together in practice. The seed in
    ``References`` is the only handle we get on the thread — Resend generates
    the ``Message-ID`` itself and the ``id`` it returns is a Resend object id,
    not an RFC one — and a reply's ``References`` puts our seed first, which is
    exactly what the inbound parser reads as the thread root. And a message
    nobody has ever seen arriving as "Re: Standup" reads as mail you have lost.
    """
    service = ResendPlatformService(
        {
            "api_key": "re_test",
            "from_address": "pod-1@ops.asur.work",
            "from_name": "Lemma",
        }
    )
    captured = {}

    async def _fake_post(self, url, json, headers):  # noqa: ANN001
        captured["json"] = json

        class _Resp:
            content = b"{}"

            def raise_for_status(self):
                return None

            def json(self):
                return {"id": "email-9"}

        return _Resp()

    seed = "<lemma-notification-abc@ops.asur.work>"
    with patch("httpx.AsyncClient.post", new=_fake_post):
        sent = await service.send_cold_email(
            recipient_email="bob@example.com",
            subject="Standup",
            message="What did you ship?",
            thread_seed_id=seed,
        )

    body = captured["json"]
    assert body["subject"] == "Standup", "first contact must not be prefixed Re:"
    assert body["headers"]["References"] == seed
    assert "In-Reply-To" not in body["headers"], "a cold open replies to nothing"
    assert sent.external_thread_id == seed
    assert sent.external_message_id == "email-9"


def test_the_parser_reads_our_seed_back_as_the_thread_root():
    """The other half of the seed contract, from the receiving side.

    Proves the two ends agree: a real MUA reply carries our seed first in
    ``References`` (original's References + original's Message-ID), and the
    parser must derive that exact string as ``external_thread_id`` — otherwise
    the reply opens a new conversation and the asker waits forever.
    """
    seed = "<lemma-notification-abc@ops.asur.work>"
    event = ResendInboundParser().parse(
        {
            "from": "Bob@Example.com",
            "to": "pod-1@ops.asur.work",
            "subject": "Re: Standup",
            "text": "Shipped the importer.",
            "message_id": "<reply-1@example.com>",
            "in_reply_to": "<resend-generated@resend.dev>",
            "references": [seed, "<resend-generated@resend.dev>"],
        }
    )

    assert event is not None
    assert event.external_thread_id == seed


# --------------------------------------------------------------------------
# Resend's ACTUAL inbound contract.
#
# The fixtures above this line were written from our normalizer rather than
# from Resend, which is why an inbound path that never once produced a message
# body passed its tests for as long as it existed. Everything below is built
# from the documented `email.received` envelope: metadata only, no body, no
# headers beyond message_id, and a separate Received Emails API call for the
# rest.
# --------------------------------------------------------------------------

SEED = "<lemma-notification-7f3a@ops.asur.work>"


def _real_webhook(**overrides) -> dict:
    """Exactly what Resend POSTs for `email.received` — no body, no headers."""
    data = {
        "email_id": "56761188-7520-42d8-8898-ff6fc54ce618",
        "created_at": "2026-02-22T23:41:11.894Z",
        "from": "Bob Jones <bob@example.com>",
        "to": ["agent.pod@ops.asur.work"],
        "cc": [],
        "bcc": [],
        "received_for": [],
        "message_id": "<reply-1@example.com>",
        "subject": "Re: Standup",
        "attachments": [],
    }
    data.update(overrides)
    return {"type": "email.received", "created_at": data["created_at"], "data": data}


def _received_email(**overrides) -> dict:
    """A GET /emails/receiving/{id} response: this is where the body lives."""
    payload = {
        "object": "email",
        "id": "56761188-7520-42d8-8898-ff6fc54ce618",
        "from": "Bob Jones <bob@example.com>",
        "to": ["agent.pod@ops.asur.work"],
        "subject": "Re: Standup",
        "text": "Shipped the importer.",
        "html": None,
        "html_format": None,
        "headers": {
            "from": "Bob Jones <bob@example.com>",
            "message-id": "<reply-1@example.com>",
            "references": f"{SEED} <resend-generated@resend.dev>",
            "in-reply-to": "<resend-generated@resend.dev>",
        },
        "message_id": "<reply-1@example.com>",
        "attachments": [],
    }
    payload.update(overrides)
    return payload


def test_the_webhook_alone_carries_no_message_and_that_is_expected():
    """Pins the provider contract, so nobody 'fixes' the parser for this again.

    Resend documents that `email.received` excludes the body, headers and
    attachments. The parser must still produce an event — the email_id on it is
    the only way to go and fetch the rest.
    """
    event = ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook()))

    assert event is not None
    assert event.message_text == ""
    assert event.metadata["email_id"] == "56761188-7520-42d8-8898-ff6fc54ce618"
    # A display-name sender must still resolve to a bare address.
    assert event.sender_external_user_id == "bob@example.com"
    assert event.sender_display_name == "Bob Jones"


def test_enrichment_supplies_the_body_the_webhook_omitted():
    event = ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook()))

    merged = merge_received_email(event, _received_email())

    assert merged is not None
    assert merged.message_text == "Shipped the importer."


def test_enrichment_recovers_the_seed_so_a_reply_threads():
    """The claim the whole notification round trip rests on.

    We seed `References` on the outbound; the reply comes back with our seed
    first. But References only exists after the fetch — before it the thread
    root collapses to a per-email message id, which is why every reply used to
    open a brand-new conversation.
    """
    event = ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook()))
    assert event.external_thread_id != SEED  # nothing to thread on yet

    merged = merge_received_email(event, _received_email())

    assert merged.external_thread_id == SEED


def test_an_html_only_reply_is_decoded_cleaned_and_unquoted():
    """One assertion per failure mode, because they all landed in the prompt.

    data_uri stayed base64, <style> contents were injected as text, every
    newline was collapsed into one line, and the quoted original was carried
    forward on every single reply.
    """
    import base64

    html = (
        b"<style>.p1{color:#000;font-family:Helvetica}</style>"
        b"<p>Shipped the importer.</p><p>Reviewed two PRs.</p>"
        b"<div>On Mon, Ops wrote:</div><blockquote>What did you ship?</blockquote>"
    )
    merged = merge_received_email(
        ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook())),
        _received_email(
            text=None,
            html="data:text/html;base64," + base64.b64encode(html).decode(),
            html_format="data_uri",
        ),
    )

    assert "Shipped the importer." in merged.message_text
    assert "Reviewed two PRs." in merged.message_text
    assert "color:#000" not in merged.message_text, "stylesheet leaked into the prompt"
    assert "What did you ship?" not in merged.message_text, "quoted original kept"
    assert "\n" in merged.message_text, "paragraph breaks collapsed"


def test_an_unfetchable_body_drops_the_event_rather_than_running_empty():
    """An empty prompt reads to the sender as the agent ignoring them."""
    event = ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook()))

    assert merge_received_email(event, {"text": None, "html": None}) is None
    assert merge_received_email(event, {}) is None


def test_a_forwarded_email_is_routed_by_who_it_was_delivered_for():
    """Under aliasing `to` is the original recipient, not the pod address.

    Matching on `to` alone silently answered "no surface for address" and the
    mail vanished.
    """
    normalized = _normalize_resend_inbound(
        _real_webhook(
            to=["someone-else@example.com"],
            received_for=["agent.pod@ops.asur.work"],
        )
    )

    assert "agent.pod@ops.asur.work" in normalized["recipients"]


def test_a_multi_valued_references_header_is_not_stringified_as_a_list():
    """Caught in live testing against a real reply, not by any fixture.

    Resend's Received Emails API returns `references` as a JSON **array**.
    Stringifying it produced `'["<a>","<b>"]'`, which survives `.split()` as one
    token, so the thread root became the entire serialized list and the reply
    opened a new conversation instead of rejoining the one it answered. RFC 5322
    defines References as whitespace-separated message ids, so joining is both
    the fix and the correct reading.
    """
    from app.modules.agent_surfaces.platforms.resend.inbound import (
        header_map,
        references_of,
    )

    headers = header_map(
        {
            "References": [
                "<lemma-notification-abc@ops.asur.work>",
                "<0106-generated@ap-northeast-1.amazonses.com>",
            ],
            "In-Reply-To": "<0106-generated@ap-northeast-1.amazonses.com>",
        }
    )

    refs = references_of({}, headers)

    assert refs == [
        "<lemma-notification-abc@ops.asur.work>",
        "<0106-generated@ap-northeast-1.amazonses.com>",
    ]
    assert not refs[0].startswith("["), "the array was stringified, not joined"


def test_a_real_reply_threads_back_onto_our_seed():
    """End-to-end of the contract, using the exact shapes Resend sent us live."""
    seed = "<lemma-notification-019fef11@ops.asur.work>"
    event = ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook()))

    merged = merge_received_email(
        event,
        _received_email(
            text="I worked on giving every agent its own email address",
            headers={
                "from": "Anukul <anukul@lemma.work>",
                "message-id": "<reply-outlook@outlook.com>",
                # The array shape, as observed from the live API.
                "references": [seed, "<generated@amazonses.com>"],
                "in-reply-to": "<generated@amazonses.com>",
            },
        ),
    )

    assert merged.external_thread_id == seed


def test_a_json_array_smuggled_inside_a_header_string_is_unwrapped():
    """The shape Resend actually returned for a live reply.

    Not a list of ids, and not a plain header — a list whose single element is a
    JSON-encoded array of ids. Joining the outer list leaves the blob intact, so
    `.split()` yields one token and the thread root becomes the whole serialized
    array. The reply then opens a new conversation and the notification it was
    answering stays OPEN forever.
    """
    from app.modules.agent_surfaces.platforms.email_identity import email_thread_root
    from app.modules.agent_surfaces.platforms.resend.inbound import (
        header_map,
        references_of,
    )

    seed = "<lemma-notification-019fef17@ops.asur.work>"
    headers = header_map(
        {
            "References": [
                f'["{seed}","<0106-generated@ap-northeast-1.amazonses.com>"]',
                "<reply-from-outlook@outlook.com>",
            ]
        }
    )

    refs = references_of({}, headers)

    assert refs[0] == seed, "the JSON array was left serialized"
    assert (
        email_thread_root(
            references=refs,
            in_reply_to=None,
            message_id="<reply-from-outlook@outlook.com>",
            sender="anukul@lemma.work",
        )
        == seed
    )


def test_a_header_that_merely_looks_like_json_is_left_alone():
    """Unwrapping must not corrupt an ordinary header that starts with '['."""
    from app.modules.agent_surfaces.platforms.resend.inbound import header_map

    headers = header_map({"Subject": "[URGENT] deploy failed]"})

    assert headers["subject"] == "[URGENT] deploy failed]"


@pytest.mark.asyncio
async def test_an_email_reply_resolves_credentials_from_its_surface():
    """`from_address` is a property of the surface, not of the platform.

    `for_platform` returns only the deployment-wide api key, so a reply routed
    through the run context had no sender address and Resend refused it with
    "requires api_key, from_address and a recipient" — which reads like missing
    configuration and is really a missing lookup. Every agent reply on an email
    surface failed this way, including the acknowledgement that closes a
    notification.
    """
    from unittest.mock import AsyncMock

    from types import SimpleNamespace

    from app.modules.agent_surfaces.services.ingress_service import (
        AgentSurfaceIngressService,
    )

    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="resend-mailtest",
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
        surface_identity_email="mailtest.acme@ops.asur.work",
    )

    service = AgentSurfaceIngressService.__new__(AgentSurfaceIngressService)
    service._uow_factory = None
    service.surface_repository = AsyncMock()
    service.surface_repository.get = AsyncMock(return_value=surface)
    service.credential_resolver = AsyncMock()
    service.credential_resolver.for_surface = AsyncMock(
        return_value={"api_key": "re_x", "from_address": surface.surface_identity_email}
    )

    context = SimpleNamespace(
        platform=SurfacePlatform.RESEND.value,
        surface_id=surface.id,
        surface_account_id=None,
    )

    credentials = await service._resolve_credentials_from_context(context)

    assert credentials["from_address"] == "mailtest.acme@ops.asur.work"
    service.credential_resolver.for_surface.assert_awaited_once_with(surface)


@pytest.mark.asyncio
async def test_chat_platforms_do_not_pay_for_an_extra_surface_read():
    """This runs on every inbound reply, so the lookup stays scoped to Resend."""
    from unittest.mock import AsyncMock

    from types import SimpleNamespace

    from app.modules.agent_surfaces.services.ingress_service import (
        AgentSurfaceIngressService,
    )

    service = AgentSurfaceIngressService.__new__(AgentSurfaceIngressService)
    service._uow_factory = None
    service.surface_repository = AsyncMock()
    service.credential_resolver = AsyncMock()
    service.credential_resolver.for_platform = AsyncMock(return_value={"token": "t"})

    context = SimpleNamespace(
        platform=SurfacePlatform.SLACK.value,
        surface_id=uuid4(),
        surface_account_id=None,
    )

    await service._resolve_credentials_from_context(context)

    service.surface_repository.get.assert_not_awaited()
    service.credential_resolver.for_platform.assert_awaited_once()


def test_references_unwrap_applies_to_the_data_field_too():
    """The sibling path the first unwrap fix missed.

    `references_of` reads a top-level `data.references` before falling back to
    headers, and that copy was read raw — so the JSON-in-a-string shape was only
    handled on one of the two routes. The test that was supposed to cover this
    passed an empty `data`, which is exactly why it did not.
    """
    from app.modules.agent_surfaces.platforms.resend.inbound import references_of

    refs = references_of({"references": '["<seed@ops.asur.work>","<gen@ses>"]'}, {})

    assert refs == ["<seed@ops.asur.work>", "<gen@ses>"]


@pytest.mark.asyncio
async def test_a_failed_body_fetch_raises_so_the_delivery_is_retried():
    """A transient mail-API error must not consume the email.

    Resend's webhook carries no body, so enrichment *is* the message. Swallowing
    the failure left an empty event, the empty-message guard dropped it, and the
    webhook had already returned 200 — so a 429 or a timeout lost somebody's
    reply permanently, with the only trace at DEBUG level.
    """
    from unittest.mock import AsyncMock, patch

    from app.modules.agent_surfaces.platforms.resend.adapter import (
        ResendSurfaceAdapter,
    )

    event = ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook()))

    with patch.object(
        ResendPlatformService,
        "fetch_received_email",
        new=AsyncMock(side_effect=RuntimeError("resend 429")),
    ):
        with pytest.raises(RuntimeError):
            await ResendSurfaceAdapter().enrich_inbound_event(
                credentials={"api_key": "re_test"}, event=event
            )


def test_the_unique_email_index_is_declared_on_the_model_too():
    """Allocation inserts and retries, which is only safe with the index there.

    It existed only in Alembic, so any schema built from metadata — tests, a
    fresh non-Alembic environment — allocated addresses unguarded, and
    autogenerate would have emitted a DROP for an index it could not see.
    """
    from app.modules.agent_surfaces.infrastructure.models import AgentSurface

    names = {index.name for index in AgentSurface.__table__.indexes}

    assert "uq_agent_surface_identity_email" in names
    index = next(
        i
        for i in AgentSurface.__table__.indexes
        if i.name == "uq_agent_surface_identity_email"
    )
    assert index.unique is True


def test_an_inbound_attachment_keeps_its_filename():
    """Resend calls it `filename`; everything downstream reads `name`.

    Unmapped, the attachment passed the identifiable check on its id alone and
    arrived nameless, so the ingest service could not save it under anything a
    person would recognise and the agent was told a file existed with no way to
    refer to it.
    """
    from app.modules.agent_surfaces.platforms.resend.inbound import (
        normalize_attachments,
    )

    [attachment] = normalize_attachments(
        [
            {
                "id": "att-1",
                "filename": "invoice.pdf",
                "content_type": "application/pdf",
                "content_disposition": "attachment",
                "size": 4096,
            }
        ]
    )

    assert attachment["name"] == "invoice.pdf"
    assert attachment["id"] == "att-1"
    assert attachment["content_type"] == "application/pdf"
    assert attachment["is_inline"] is False


def test_an_inline_image_is_marked_inline():
    """Signature logos arrive as attachments; the agent should not treat them
    as files the person deliberately sent."""
    from app.modules.agent_surfaces.platforms.resend.inbound import (
        normalize_attachments,
    )

    [attachment] = normalize_attachments(
        [{"id": "a", "filename": "logo.png", "content_disposition": "inline"}]
    )

    assert attachment["is_inline"] is True


@pytest.mark.asyncio
async def test_an_attachment_is_downloaded_through_its_signed_url():
    """Resend serves attachment bytes from a signed CDN URL, not from its API.

    Two hops: the attachment endpoint returns metadata with `download_url`, and
    the content comes from there. The adapter previously had no
    `download_attachment` at all, so inbound files were named and never fetched.
    """
    import httpx
    from unittest.mock import patch

    from app.modules.agent_surfaces.platforms.resend.adapter import (
        ResendSurfaceAdapter,
    )

    event = ResendInboundParser().parse(
        _normalize_resend_inbound(
            _real_webhook(
                attachments=[
                    {
                        "id": "att-1",
                        "filename": "invoice.pdf",
                        "content_type": "application/pdf",
                        "size": 10,
                    }
                ]
            )
        )
    )
    seen: dict = {}

    class _Resp:
        def __init__(self, payload=None, content=b""):
            self._payload, self.content = payload, content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    async def _fake_get(self, url, **kwargs):  # noqa: ANN001
        seen.setdefault("urls", []).append(url)
        seen.setdefault("auth", []).append(
            (kwargs.get("headers") or {}).get("Authorization")
        )
        if "/attachments/" in url:
            return _Resp(
                {
                    "filename": "invoice.pdf",
                    "content_type": "application/pdf",
                    "download_url": "https://cdn.resend.test/signed",
                },
                b"{}",
            )
        return _Resp(None, b"%PDF-1.4 real bytes")

    with patch.object(httpx.AsyncClient, "get", new=_fake_get):
        downloaded = await ResendSurfaceAdapter().download_attachment(
            credentials={"api_key": "re_test"},
            event=event,
            attachment=event.metadata["attachments"][0],
        )

    content, name, mime = downloaded
    assert content == b"%PDF-1.4 real bytes"
    assert name == "invoice.pdf"
    assert mime == "application/pdf"
    # The API call is authenticated; the signed URL must not carry our key.
    assert seen["auth"][0] == "Bearer re_test"
    assert seen["auth"][1] is None


# ------------------------------------- a body we already hold is never lost


def _webhook_carrying_a_body() -> dict:
    """A delivery that already includes what the person wrote.

    Observed in production: the `email.received` payload arrived with `text`,
    `html` *and* `headers` — the `references` seed among them. Fetching the body
    again is then a second chance to fail, not a way to learn anything.
    """
    return _real_webhook(
        text="Let's do it today?",
        html="<div>Let's do it today?</div>",
        headers={
            "message-id": "<reply-1@example.com>",
            "references": '["<lemma-notification-abc@ops.asur.work>"]',
            "subject": "Re: Standup",
        },
    )


@pytest.mark.asyncio
async def test_a_restricted_api_key_does_not_lose_a_reply_we_can_already_read():
    """The dev incident, as a test.

    A Resend key restricted to sending answers `GET /emails/receiving` with 401.
    Enrichment fetched unconditionally, the failure propagated, and the reply was
    dropped and retried eight times — while the webhook had handed us the body in
    full and the person who wrote it heard nothing back.
    """
    import httpx
    from unittest.mock import patch

    from app.modules.agent_surfaces.platforms.resend.adapter import (
        ResendSurfaceAdapter,
    )

    event = ResendInboundParser().parse(
        _normalize_resend_inbound(_webhook_carrying_a_body())
    )
    refused = httpx.HTTPStatusError(
        "401",
        request=httpx.Request("GET", "https://api.resend.com/emails/receiving/x"),
        response=httpx.Response(
            401,
            json={
                "statusCode": 401,
                "message": "This API key is restricted to only send emails",
                "name": "restricted_api_key",
            },
        ),
    )

    with patch.object(
        ResendPlatformService,
        "fetch_received_email",
        new=AsyncMock(side_effect=refused),
    ):
        enriched = await ResendSurfaceAdapter().enrich_inbound_event(
            credentials={"api_key": "re_test"}, event=event
        )

    assert enriched is not None
    assert "Let's do it today?" in enriched.message_text
    # The seed the reply threads on came from the webhook's own headers, so the
    # conversation is still found without the fetch.
    assert enriched.external_thread_id == "<lemma-notification-abc@ops.asur.work>"


@pytest.mark.asyncio
async def test_a_failed_fetch_still_raises_when_there_is_no_body_to_keep():
    """The original guarantee, unchanged.

    When the webhook carries metadata only, enrichment *is* the message — a
    transient failure must retry rather than run an agent on an empty prompt.
    """
    from unittest.mock import AsyncMock, patch

    from app.modules.agent_surfaces.platforms.resend.adapter import (
        ResendSurfaceAdapter,
    )

    event = ResendInboundParser().parse(_normalize_resend_inbound(_real_webhook()))

    with patch.object(
        ResendPlatformService,
        "fetch_received_email",
        new=AsyncMock(side_effect=OSError("connection reset")),
    ):
        with pytest.raises(OSError):
            await ResendSurfaceAdapter().enrich_inbound_event(
                credentials={"api_key": "re_test"}, event=event
            )


def test_a_provider_refusal_is_reported_by_status_and_name():
    """What has to reach the log, since ``error`` fields are stripped from it.

    ``restricted_api_key`` names the fix on its own; "enrichment failed" sent us
    to Log Analytics, then to a database, then to the provider's API.
    """
    import httpx

    from app.modules.agent_surfaces.platforms.common import provider_failure

    failure = provider_failure(
        httpx.HTTPStatusError(
            "401",
            request=httpx.Request("GET", "https://api.resend.com/x"),
            response=httpx.Response(401, json={"name": "restricted_api_key"}),
        )
    )

    assert failure.status_code == 401
    assert failure.provider_error == "restricted_api_key"
    assert failure.failure_type == "HTTPStatusError"
    # No free text: a provider message can carry a key or somebody's address.
    assert "restricted to only send" not in str(failure)


def test_a_non_http_failure_still_reports_something_usable():
    from app.modules.agent_surfaces.platforms.common import provider_failure

    failure = provider_failure(OSError("connection reset"))

    assert failure.failure_type == "OSError"
    assert failure.status_code is None
    assert failure.provider_error is None
