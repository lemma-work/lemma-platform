"""E2E for notifications: the full recipient lifecycle over the real HTTP API,
against a real database.

The claim under test is the one the whole feature rests on — *a notification
owns the ask from creation until it resolves, and it resolves exactly once*. So
these exercise the illegal moves as hard as the legal ones: answering twice,
dismissing something that owes an answer, free-texting a workflow form. Each is
what a second browser tab, a retried worker job, or an impatient double-click
will actually attempt.

Delivery is asserted the same way. ``UNDELIVERABLE`` is a *success* here — the
pod has no surface the recipient has ever used, so no chat app could carry it,
and the point is that the notification exists and the inbox has it regardless.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.e2e


async def _notify(
    client: AsyncClient, pod_id: str, recipient: str, **overrides
) -> dict:
    payload = {
        "recipient": recipient,
        "title": "Standup",
        "body": "What did you ship yesterday?",
        "background_instruction": "Record their update as the response summary.",
        "expects_response": True,
    }
    payload.update(overrides)
    response = await client.post(f"/pods/{pod_id}/notifications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_notification_lifecycle_from_send_to_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]

    created = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )

    # No chat surface exists in this pod, so nothing could carry it — and that is
    # explicitly not a failure. The row exists, the inbox has it, and the reason
    # is phrased for a human to act on.
    assert created["delivery_status"] == "UNDELIVERABLE"
    assert created["undeliverable_reason"]
    assert created["status"] == "OPEN"
    assert created["awaiting_response"] is True
    assert created["responds_through_action"] is False

    # The background instruction is for the agent that handles the reply, never
    # for the recipient — it carries the asker's private framing.
    assert "background_instruction" not in created

    listed = await authenticated_client.get(f"/pods/{pod_id}/notifications")
    assert listed.status_code == 200, listed.text
    ids = [item["id"] for item in listed.json()["items"]]
    assert created["id"] in ids

    unread = await authenticated_client.get(
        f"/pods/{pod_id}/notifications/unread-count"
    )
    assert unread.json()["unread"] >= 1

    answered = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{created['id']}/respond",
        json={"summary": "Shipped the importer and reviewed two PRs."},
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["status"] == "RESPONDED"
    assert body["response_summary"] == "Shipped the importer and reviewed two PRs."
    assert body["awaiting_response"] is False
    # Answering implies having seen it; a badge that survives an answer is noise.
    assert body["read_at"] is not None


async def test_a_notification_can_only_be_answered_once(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """Two tabs, or a double-click, must not overwrite an answer already acted on."""
    pod_id = test_pod["id"]
    created = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )

    first = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{created['id']}/respond",
        json={"summary": "first answer"},
    )
    assert first.status_code == 200

    second = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{created['id']}/respond",
        json={"summary": "second answer"},
    )
    assert second.status_code == 409, second.text

    current = await authenticated_client.get(f"/pods/{pod_id}/notifications")
    row = next(i for i in current.json()["items"] if i["id"] == created["id"])
    assert row["response_summary"] == "first answer"


async def test_dismissing_something_that_owes_an_answer_is_refused(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    owed = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )
    refused = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{owed['id']}/acknowledge"
    )
    assert refused.status_code == 409, refused.text

    fyi = await _notify(
        authenticated_client,
        pod_id,
        recipient=fixed_test_user["email"],
        title="Nightly build finished",
        body="All green.",
        expects_response=False,
    )
    assert fyi["awaiting_response"] is False
    ack = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{fyi['id']}/acknowledge"
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "ACKNOWLEDGED"

    # And the reverse: nothing was asked, so there is nothing to answer.
    responded = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{fyi['id']}/respond",
        json={"summary": "unsolicited"},
    )
    assert responded.status_code == 409


async def test_reading_is_not_answering(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    created = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )

    read = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{created['id']}/read"
    )
    assert read.status_code == 200, read.text
    assert read.json()["read_at"] is not None
    # Still owed. The two axes are independent.
    assert read.json()["status"] == "OPEN"
    assert read.json()["awaiting_response"] is True


async def test_mark_all_read_clears_the_badge_without_answering_anything(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    for index in range(3):
        await _notify(
            authenticated_client,
            pod_id,
            recipient=fixed_test_user["email"],
            title=f"Question {index}",
        )

    cleared = await authenticated_client.post(f"/pods/{pod_id}/notifications/read-all")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["unread"] == 0

    still_open = await authenticated_client.get(
        f"/pods/{pod_id}/notifications", params={"status": "OPEN"}
    )
    assert len(still_open.json()["items"]) >= 3


async def test_a_recipient_outside_the_pod_is_refused(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
):
    """404, not 422: an id naming nobody and an id naming somebody outside the
    pod are the same fact from the caller's side, and separating them would
    confirm the person exists."""
    pod_id = test_pod["id"]
    response = await authenticated_client.post(
        f"/pods/{pod_id}/notifications",
        json={
            "recipient": str(uuid4()),
            "title": "Nope",
            "body": "Should not arrive.",
        },
    )
    assert response.status_code == 404, response.text


async def test_only_the_recipient_can_see_their_notifications(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """A notification body is often exactly the thing a colleague should not read."""
    pod_id = test_pod["id"]
    created = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )

    missing = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{uuid4()}/respond",
        json={"summary": "whatever"},
    )
    assert missing.status_code == 404, missing.text
    assert created["id"]


async def test_a_workflow_form_assignment_notifies_and_closes_on_submit(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """Assigning a FORM now *tells* the assignee, and submitting closes the ask.

    Before this, a FORM wait was a pure pull queue — the row existed, the
    "waiting for you" list rendered it, and nobody was ever told, so a run could
    sit for days on someone who had no idea.

    The notification is answered by submitting the form (which validates against
    the node's schema), not by free text, so ``respond`` must refuse it: two
    answer paths where only one validates is the bug this prevents.
    """
    from sqlalchemy import select

    from app.modules.pod.infrastructure.models.pod_models import PodMember
    from app.modules.identity.infrastructure.models.organization_models import (
        OrganizationMember,
    )

    pod_id = test_pod["id"]

    member_id = (
        await db_session.execute(
            select(PodMember.id)
            .join(
                OrganizationMember,
                OrganizationMember.id == PodMember.organization_member_id,
            )
            .where(
                PodMember.pod_id == UUID(pod_id),
                OrganizationMember.user_id == UUID(str(fixed_test_user["id"])),
            )
        )
    ).scalar_one()

    created = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": "expense-approval",
            "start": {"type": "MANUAL"},
            "mode": "GLOBAL",
        },
    )
    assert created.status_code == 201, created.text
    workflow_name = created.json()["name"]

    graph = await authenticated_client.put(
        f"/pods/{pod_id}/workflows/{workflow_name}/graph",
        json={
            "start": {"type": "MANUAL"},
            "nodes": [
                {
                    "id": "approve",
                    "type": "FORM",
                    "label": "Approve the expense",
                    "config": {
                        "assignee_pod_member_id": str(member_id),
                        "input_schema": {
                            "type": "object",
                            "properties": {"approved": {"type": "boolean"}},
                            "required": ["approved"],
                        },
                    },
                },
                {"id": "end", "type": "END"},
            ],
            "edges": [{"id": "e1", "source": "approve", "target": "end"}],
        },
    )
    assert graph.status_code == 200, graph.text

    run = await authenticated_client.post(
        f"/pods/{pod_id}/workflows/{workflow_name}/runs"
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    inbox = await authenticated_client.get(f"/pods/{pod_id}/notifications")
    assert inbox.status_code == 200, inbox.text
    form_notifications = [
        item
        for item in inbox.json()["items"]
        if item["origin_kind"] == "WORKFLOW_FORM" and item["origin_id"] == run_id
    ]
    assert len(form_notifications) == 1, inbox.text
    notification = form_notifications[0]

    # It points at the real form, so the inbox can render it rather than
    # deep-linking away to go and find it.
    assert notification["responds_through_action"] is True
    assert notification["action"]["node_id"] == "approve"
    assert notification["action"]["run_id"] == run_id
    assert notification["action"]["schema"]["properties"]["approved"]

    refused = await authenticated_client.post(
        f"/pods/{pod_id}/notifications/{notification['id']}/respond",
        json={"summary": "looks fine to me"},
    )
    assert refused.status_code == 409, refused.text

    submitted = await authenticated_client.post(
        f"/pods/{pod_id}/workflow-runs/{run_id}/form",
        json={"node_id": "approve", "inputs": {"approved": True}},
    )
    assert submitted.status_code == 200, submitted.text

    after = await authenticated_client.get(f"/pods/{pod_id}/notifications")
    closed = next(
        item for item in after.json()["items"] if item["id"] == notification["id"]
    )
    assert closed["status"] == "RESPONDED"
    assert closed["awaiting_response"] is False


async def test_a_notification_cold_opens_an_email_thread_the_reply_can_find(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """The first test in this file that reaches the delivery path at all.

    Every other test here runs in a pod with no surface, so ``deliver()``
    returns at the ``UNDELIVERABLE`` branch and never calls into egress. That is
    precisely how two ``AttributeError``s — a missing ``agent_name_for_surface``
    and a missing cold-email send — shipped and stayed invisible.

    So this asserts the whole loop end to end: the mail goes out, it is *not*
    prefixed "Re:", it carries a seed in ``References``, and a reply quoting
    that seed lands in the same conversation the notification opened, with the
    asker's ``background_instruction`` still attached to it.
    """
    import json
    from uuid import UUID as _UUID

    from app.modules.agent_surfaces.domain.ingress_request import (
        SurfacePlatformWebhookIngress,
    )
    from app.modules.agent_surfaces.infrastructure.models import AgentSurface
    from app.modules.agent_surfaces.tests.e2e.helpers import (
        _conversation_by_external_thread,
        _create_surface,
        _ensure_connector_account,
        _resend_payload,
    )
    from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
        wait_for_messages,
    )
    from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
        script_text,
        process_ingress_and_run_scripted,
    )
    from app.modules.connectors.domain.connector import AuthProvider

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": fake_resend.api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, _UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    created = await _notify(
        authenticated_client, pod_id, recipient=fixed_test_user["email"]
    )

    # Not UNDELIVERABLE — the whole point.
    assert created["delivery_status"] == "DELIVERED", created
    assert created["delivery_platform"] == "RESEND"

    sent = (await wait_for_messages(message_store, "RESEND", min_count=1))[-1]
    body = json.loads(json.dumps(sent))
    # First contact is not a reply, and must not read as one.
    assert "Re:" not in json.dumps(body.get("subject", ""))
    seed = json.dumps(body).split('"References": "')[1].split('"')[0]
    assert seed.startswith("<lemma-notification-")

    # Their MUA answers with References = [our seed, their message id], so the
    # parser's thread root is our seed.
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload={
                **_resend_payload(
                    sender_email=fixed_test_user["email"],
                    assistant_address=assistant_address,
                    message_id="reply-to-standup",
                    text="Shipped the importer and reviewed two PRs.",
                ),
                "references": [seed, "<reply-to-standup@resend-e2e.test>"],
            },
            headers={},
        ),
        script=[script_text("Thanks, recorded.")],
    )

    threaded = await _conversation_by_external_thread(
        authenticated_client, pod_id=pod_id, external_thread_id=seed
    )
    assert threaded is not None, "the reply did not land in the notification's thread"
    assert (threaded.get("metadata") or {}).get("notification_id") == created["id"]


async def test_a_pod_with_nothing_connected_mints_itself_a_readable_mailbox(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The dev failure, against a real database and the real surface service.

    A personal pod with no surfaces at all, asked to message a member, reported
    "the pod has no active surface to reach anyone on" and created nothing. The
    unit tests around this stub the provisioner, so they can prove routing
    *asks* for a mailbox but not that a usable one comes back.

    This asserts the surface that actually lands: owned by the pod's own
    assistant rather than any named agent, and carrying an address a person
    could be asked to type.
    """
    from sqlalchemy import select

    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.infrastructure.models import AgentSurface

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")

    pod_id = test_pod["id"]
    before = (
        (
            await db_session.execute(
                select(AgentSurface).where(AgentSurface.pod_id == UUID(pod_id))
            )
        )
        .scalars()
        .all()
    )
    assert before == [], "this test is only meaningful in a pod with no surfaces"

    created = await _notify(authenticated_client, pod_id, fixed_test_user["id"])

    # Not undeliverable for want of a surface: one was created to carry it.
    assert created["undeliverable_reason"] != (
        "The pod has no active surface to reach anyone on."
    )

    await db_session.commit()
    surfaces = (
        (
            await db_session.execute(
                select(AgentSurface).where(AgentSurface.pod_id == UUID(pod_id))
            )
        )
        .scalars()
        .all()
    )

    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface.surface_type == "RESEND"
    # The pod's own, not an agent's — this is the surface the assistant uses.
    # The assistant's, whose row id is its pod's. It used to be *nobody's*,
    # which is what made one column mean two things.
    assert surface.agent_id == UUID(pod_id)
    assert surface.surface_identity_email.endswith("@ops.example.com")
    # Readable, not pod-<32 hex chars>: people are asked to write to this.
    local_part = surface.surface_identity_email.split("@")[0]
    assert not local_part.startswith("pod-")


async def test_a_second_pod_in_the_org_also_gets_a_mailbox(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The dev failure exactly: the org already has a mailbox, this pod wants one.

    ``ensure_unique_org_credential_binding`` treated the system Resend key as a
    claimable identity, like a Slack app or a WhatsApp number. It is not — it is
    one API key over a catch-all domain, and every surface gets its own unique
    address off it. So the first mailbox created anywhere in an organization
    refused every mailbox after it, and the pod assistant that asked for one was
    told "creating a mailbox for it failed" with the cause stripped from the log.

    Two pods, one organization, one notification each.
    """
    from sqlalchemy import select

    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.infrastructure.models import AgentSurface

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")

    second = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Second Pod {uuid4()}",
            "slug": f"second-pod-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert second.status_code in (200, 201), second.text
    second_pod_id = second.json()["id"]

    # The first pod claims a mailbox, as it would on any deployment.
    await _notify(authenticated_client, test_pod["id"], fixed_test_user["id"])
    # The second pod asks for one while that claim exists.
    created = await _notify(authenticated_client, second_pod_id, fixed_test_user["id"])

    assert "creating a mailbox" not in (created["undeliverable_reason"] or "")

    await db_session.commit()
    addresses = {
        row.pod_id: row.surface_identity_email
        for row in (
            await db_session.execute(
                select(AgentSurface).where(
                    AgentSurface.pod_id.in_([UUID(test_pod["id"]), UUID(second_pod_id)])
                )
            )
        )
        .scalars()
        .all()
    }

    assert len(addresses) == 2, "both pods should hold a mailbox of their own"
    # Distinct addresses are what makes sharing the key safe: inbound routes on
    # the address, which carries a unique index.
    assert len(set(addresses.values())) == 2


async def test_resend_mailbox_is_blocked_on_a_local_url_without_polling(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The desktop-app failure, reproduced against the real surface service.

    On a localhost API URL with no public webhook and polling mode off, the
    Resend mailbox cannot be provisioned (the runtime gate demands a public
    HTTPS callback), so ``message_user`` is UNDELIVERABLE and no surface lands.
    """
    from sqlalchemy import select

    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.infrastructure.models import AgentSurface

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")
    # Override the e2e's public HTTPS URL back to the desktop app's reality.
    monkeypatch.setattr(core_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", False)

    pod_id = test_pod["id"]
    created = await _notify(authenticated_client, pod_id, fixed_test_user["id"])

    assert created["delivery_status"] == "UNDELIVERABLE"
    assert "creating a mailbox for it failed" in (created["undeliverable_reason"] or "")

    await db_session.commit()
    surfaces = (
        (
            await db_session.execute(
                select(AgentSurface).where(AgentSurface.pod_id == UUID(pod_id))
            )
        )
        .scalars()
        .all()
    )
    assert surfaces == [], "no surface should be provisioned when the gate blocks it"


async def test_resend_mailbox_is_minted_on_a_local_url_with_polling(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The fix: ENABLE_RESEND_POLLING_MODE lets the desktop app (localhost, no
    public webhook) mint a Resend mailbox, so ``message_user`` is deliverable.
    """
    from sqlalchemy import select

    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.infrastructure.models import AgentSurface

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")
    monkeypatch.setattr(core_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", True)

    pod_id = test_pod["id"]
    created = await _notify(authenticated_client, pod_id, fixed_test_user["id"])

    # The mailbox was minted, so this is not the "no surface / provision failed"
    # branch. Same localhost URL as the test above — only polling mode differs.
    assert "creating a mailbox for it failed" not in (
        created["undeliverable_reason"] or ""
    )

    await db_session.commit()
    surfaces = (
        (
            await db_session.execute(
                select(AgentSurface).where(AgentSurface.pod_id == UUID(pod_id))
            )
        )
        .scalars()
        .all()
    )
    assert len(surfaces) == 1
    assert surfaces[0].surface_type == "RESEND"
    assert surfaces[0].surface_identity_email.endswith("@ops.example.com")
