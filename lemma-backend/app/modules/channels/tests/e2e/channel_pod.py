"""The pod the channels e2e tests use, and the people in it.

Four members: the owner (admin), an editor (sees and writes to contacts,
manages channels), a user (writes in channels, sees no contacts) and a viewer
(reads).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import (
    AccountKey,
    Asker,
    ContactsHome,
    NewLine,
    Place,
    Sender,
    Source,
    SourceKind,
    post,
    source_for,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import auth_headers
from app.modules.test_support.e2e_base import extend_module_identity


class Pod:
    """The pod under test and the people in it."""

    def __init__(self, scenario) -> None:
        self.scenario = scenario
        self.id = scenario.pod_id
        self.owner = scenario.owner_user
        self.base = f"/pods/{self.id}/channels"

    def client(self, who: dict | None = None):
        """A client acting as ``who`` (the owner by default)."""
        if who is None or who is self.owner:
            return _As(self.scenario.owner_client, {})
        return _As(self.scenario.async_client, auth_headers(who))


class _As:
    def __init__(self, client, headers: dict[str, str]) -> None:
        self._client, self._headers = client, headers

    def __getattr__(self, verb: str):
        call = getattr(self._client, verb)

        async def request(url: str, **kwargs):
            headers = {**self._headers, **kwargs.pop("headers", {})}
            return await call(url, headers=headers, **kwargs)

        return request


@pytest.fixture(scope="module")
async def channel_world(e2e_settings, module_identity):
    """The organization, pod and three members, made once for the module.

    Three sign-ups and an organization were most of every channels test's
    setup. They are made once here and join the module identity's snapshot,
    so each test starts from exactly these rows and nothing an earlier test
    wrote -- the reset that gave a test its own pod now gives it this one.
    """
    owner = module_identity.user

    async def build(client):
        client.headers.update(auth_headers(owner))
        scenario = E2EScenario(
            owner_client=client, async_client=client, owner_user=owner
        )
        await scenario.create_org_with_pod(name_prefix="Channels v2")
        # Signed up together: no sign-up depends on another.
        people = await asyncio.gather(
            *(scenario.create_user(f"chv2-{name}") for _, name in _SEATS)
        )
        for (role, _), person in zip(_SEATS, people, strict=True):
            await scenario.add_user_to_pod(user=person, role=role)
        # SuperTokens ids; the rows' own ids are found by the snapshot.
        return (scenario.organization, scenario.pod, people), [p["id"] for p in people]

    return await extend_module_identity(e2e_settings.database_url, build)


_SEATS = (("POD_EDITOR", "editor"), ("POD_USER", "user"), ("POD_VIEWER", "viewer"))


@pytest.fixture
async def pod(scenario, channel_world, module_identity, db_manager) -> Pod:
    del db_manager  # ordering only: the reset restores the world first
    # The world is the module user's; a module binding `fixed_test_user` to a
    # fresh user would hand these tests someone else's pod.
    assert scenario.owner_user["id"] == module_identity.user["id"]
    organization, pod_row, people = channel_world
    scenario.organization, scenario.pod = organization, pod_row
    p = Pod(scenario)
    for (_, name), person in zip(_SEATS, people, strict=True):
        setattr(p, name, person)
    return p


async def group(pod: Pod, *, as_user=None, **body) -> dict:
    made = await pod.client(as_user).post(pod.base, json={"title": "launch", **body})
    assert made.status_code == 201, made.text
    return made.json()


async def keep_answers_to_the_asker(pod: Pod, channel_id) -> dict:
    """The group keeps an agent's answer made with a member's own access to
    that member until they share it (``answers_shared`` off)."""
    changed = await pod.client().patch(
        f"{pod.base}/{channel_id}", json={"answers_shared": False}
    )
    assert changed.status_code == 200, changed.text
    return changed.json()


async def say(pod: Pod, channel_id, text: str = "hi", *, as_user=None, **body) -> dict:
    said = await pod.client(as_user).post(
        f"{pod.base}/{channel_id}/messages", json={"text": text, **body}
    )
    assert said.status_code == 201, said.text
    return said.json()


async def contact_place(
    db_session: AsyncSession, pod: Pod, *, keeper: dict | None = None
) -> tuple[Source, UUID]:
    """Dana, a contact who wrote on Telegram, and her channel's place;
    ``keeper``, the member looking after the bot's contacts."""
    uow = SqlAlchemyUnitOfWork(db_session)
    contact = await open_contact(
        uow,
        pod_id=UUID(pod.id),
        kind=IdentityKind.TELEGRAM,
        value="5550001",
        strength=IdentityStrength.CHANNEL,
        display_name="Dana",
    )
    source = await source_for(
        uow,
        pod_id=UUID(pod.id),
        place=Place(
            surface_id=None,
            platform="TELEGRAM",
            account=AccountKey(workspace="bot-1"),
            kind=SourceKind.PERSON,
            external_channel_id=None,
            external_user_id="5550001",
        ),
        home=ContactsHome(
            contact.id, keeper_user_id=UUID(keeper["id"]) if keeper else None
        ),
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.contact(contact.id),
            text="Where is my order?",
            source_id=source.id,
        ),
    )
    await db_session.commit()
    return source, contact.id


async def an_asker(pod_id: UUID, user_id: UUID) -> Asker:
    """What asks a test's question: a conversation of the pod's, made (and
    committed) for it -- the asker a question names must exist, or the
    sweeper cancels it as orphaned."""
    from app.core.infrastructure.db.session import async_session_maker
    from app.modules.agent.infrastructure.models.conversation import (
        ConversationModel,
    )

    async with async_session_maker() as session:
        conversation = ConversationModel(user_id=user_id, pod_id=pod_id)
        session.add(conversation)
        await session.commit()
        return Asker.message(conversation.id, uuid4().hex)
