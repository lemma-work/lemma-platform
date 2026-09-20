"""The available-surfaces catalog builder joins registry + connector + native creds."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_connectors import (
    SURFACE_CONNECTOR_BINDINGS,
    surface_connector_id,
)
from app.modules.agent_surfaces.services import available_surfaces_builder as mod
from app.modules.agent_surfaces.services.available_surfaces_builder import (
    build_available_surfaces,
)
from app.modules.connectors.contracts.surfaces import (
    SurfaceConnectCapability,
    SurfaceConnector,
)
from app.modules.connectors.domain.connector import (
    AuthScheme,
    HttpKindSpec,
)

_CUSTOM = SurfaceCredentialMode.CUSTOM
_SYSTEM = SurfaceCredentialMode.SYSTEM
_NATIVE = {SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM, SurfacePlatform.RESEND}


def _default_cap() -> HttpKindSpec:
    return HttpKindSpec(auth_scheme=AuthScheme.OAUTH2, system_default_available=True)


def _catalog(*, missing=(), inactive=(), no_lemma=(), capability=None):
    """Connectors' catalog read, answering for every surface connector."""
    cap = capability or _default_cap()

    async def _get(connector_id: str) -> SurfaceConnector | None:
        if connector_id in missing:
            return None
        return SurfaceConnector(
            connector_id=connector_id,
            title=connector_id.replace("_", " ").title(),
            description=None,
            icon=f"{connector_id}.png",
            is_active=connector_id not in inactive,
            connect=(
                None
                if connector_id in no_lemma
                else SurfaceConnectCapability(
                    auth_scheme=cap.auth_scheme,
                    auth_config_schema=cap.auth_config_schema,
                    credential_schema=cap.credential_schema,
                    system_oauth_available=cap.system_default_available,
                    supports_org_custom_oauth=cap.supports_org_custom_oauth,
                )
            ),
        )

    return _get


def _by_platform(resp) -> dict[SurfacePlatform, object]:
    return {s.platform: s for s in resp.surfaces}


async def test_modes_reflect_native_credentials(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    surfaces = _by_platform(await build_available_surfaces(read_connector=_catalog()))
    for platform in _NATIVE:
        assert surfaces[platform].supported_credential_modes == [_CUSTOM, _SYSTEM]
    for platform in (
        SurfacePlatform.SLACK,
        SurfacePlatform.TEAMS,
    ):
        assert surfaces[platform].supported_credential_modes == [_CUSTOM]


async def test_modes_drop_system_when_no_native_credentials(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resp = await build_available_surfaces(read_connector=_catalog())
    for surface in resp.surfaces:
        assert surface.supported_credential_modes == [_CUSTOM]


async def test_connect_descriptor_maps_capability(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    cap = HttpKindSpec(
        auth_scheme=AuthScheme.API_KEY,
        credential_schema={"type": "object"},
        auth_config_schema={"x": 1},
        system_default_available=True,
        supports_org_custom_oauth=True,
    )
    resp = await build_available_surfaces(read_connector=_catalog(capability=cap))
    teams = _by_platform(resp)[SurfacePlatform.TEAMS]
    assert teams.connector_id == "microsoft_teams"
    assert teams.connector_available is True
    assert teams.connect is not None
    assert teams.connect.auth_scheme == AuthScheme.API_KEY
    assert teams.connect.credential_schema == {"type": "object"}
    assert teams.connect.auth_config_schema == {"x": 1}
    assert teams.connect.system_oauth_available is True
    assert teams.connect.supports_org_custom_oauth is True


async def test_missing_connector_marked_unavailable(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    telegram = surface_connector_id(SurfacePlatform.TELEGRAM)
    resp = await build_available_surfaces(read_connector=_catalog(missing={telegram}))
    surface = _by_platform(resp)[SurfacePlatform.TELEGRAM]
    assert surface.connector_available is False
    assert surface.connect is None
    # Registry-derived fields survive so the platform is still visible.
    assert surface.connector_id == telegram
    assert surface.supported_credential_modes == [_CUSTOM]
    assert len(resp.surfaces) == len(SURFACE_CONNECTOR_BINDINGS)


async def test_inactive_connector_marked_unavailable(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    slack = surface_connector_id(SurfacePlatform.SLACK)
    resp = await build_available_surfaces(read_connector=_catalog(inactive={slack}))
    surface = _by_platform(resp)[SurfacePlatform.SLACK]
    assert surface.connector_available is False
    assert surface.connect is None


async def test_no_lemma_capability_does_not_raise(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resend = surface_connector_id(SurfacePlatform.RESEND)
    resp = await build_available_surfaces(read_connector=_catalog(no_lemma={resend}))
    surface = _by_platform(resp)[SurfacePlatform.RESEND]
    assert surface.connector_available is False
    assert surface.connect is None


def _claim_repository(conflict=None, *, raises=False, pool: bool = True) -> AsyncMock:
    """The repository the claim lookup runs on, and the pool it can see.

    ``pool`` says whether this deployment owns an allocatable WhatsApp number.
    The claim question is "will this surface have an identity of its own", and
    for WhatsApp that is the pool -- so it is a parameter here rather than an
    accident of what a mock happens to return.
    """
    repo = AsyncMock()
    if raises:
        repo.get_system_credential_conflict_in_org.side_effect = OperationalError(
            "SELECT 1", {}, Exception("connection reset")
        )
    else:
        repo.get_system_credential_conflict_in_org.return_value = conflict
    # `any_allocatable` reads one id through the session this uow carries.
    repo.uow.session.scalar.return_value = uuid4() if pool else None
    return repo


async def test_system_claim_absent_for_platforms_without_a_system_mode(monkeypatch):
    # No SYSTEM mode means there is no shared identity to claim, so the field is
    # None rather than a misleading "available".
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resp = await build_available_surfaces(
        read_connector=_catalog(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(),
    )
    assert all(surface.system_claim is None for surface in resp.surfaces)


async def test_system_claim_available_when_org_has_not_claimed_it(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    resp = await build_available_surfaces(
        read_connector=_catalog(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(None),
    )
    claim = _by_platform(resp)[SurfacePlatform.TELEGRAM].system_claim
    assert claim is not None
    assert claim.available is True
    assert claim.claimed_by_pod_id is None


async def test_the_shared_bot_shows_as_taken_once_the_org_holds_it(monkeypatch):
    """This asserted the opposite while WhatsApp and Telegram were exempt.

    The exemption sat on both sides -- here and in the writer -- so the two
    agreed, and what they agreed on was that the rule did not apply to the
    platforms whose system credential most plainly is an identity. The catalog's
    job is to name who holds it before somebody tries and is refused.

    Telegram rather than WhatsApp now, and the swap is the point: there is one
    shared Telegram bot and holding it is holding it. WhatsApp numbers come from
    a pool, so its system credential stopped being a single identity -- see the
    scenario below.
    """
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    holder_pod_id = uuid4()
    conflict = SimpleNamespace(pod_id=holder_pod_id, name="telegram")
    monkeypatch.setattr(mod, "AgentSurfaceEntity", SimpleNamespace)
    resp = await build_available_surfaces(
        read_connector=_catalog(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(conflict),
    )
    claim = _by_platform(resp)[SurfacePlatform.TELEGRAM].system_claim
    assert claim is not None
    assert claim.available is False
    assert claim.claimed_by_pod_id == holder_pod_id
    assert claim.claimed_by_surface_name == "telegram"


async def test_system_claim_degrades_to_available_when_lookup_fails(monkeypatch):
    # A catalog read must never fail on a repository hiccup; the write path
    # still enforces the claim, so optimistic is the safe direction.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    resp = await build_available_surfaces(
        read_connector=_catalog(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(raises=True),
    )
    claim = _by_platform(resp)[SurfacePlatform.TELEGRAM].system_claim
    assert claim is not None and claim.available is True


async def test_system_claim_skipped_without_pod_context(monkeypatch):
    # The builder stays usable as a pure registry join.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    resp = await build_available_surfaces(read_connector=_catalog())
    claim = _by_platform(resp)[SurfacePlatform.TELEGRAM].system_claim
    assert claim is not None and claim.available is True


async def test_managed_setup_offered_only_where_a_manager_bot_exists(monkeypatch):
    # Telegram can hand a user their own bot, but only where this deployment has
    # a manager bot configured — otherwise the option must not be offered.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    monkeypatch.setattr(
        mod.surface_settings, "telegram_manager_bot_token", "123:abc", raising=False
    )
    monkeypatch.setattr(
        mod.surface_settings,
        "telegram_manager_bot_username",
        "lemma_manager",
        raising=False,
    )
    surfaces = _by_platform(await build_available_surfaces(read_connector=_catalog()))
    assert surfaces[SurfacePlatform.TELEGRAM].managed_setup_available is True
    # It is a Telegram-only path; nothing else claims it.
    assert all(
        surface.managed_setup_available is False
        for platform, surface in surfaces.items()
        if platform is not SurfacePlatform.TELEGRAM
    )


async def test_managed_setup_hidden_without_a_manager_bot(monkeypatch):
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    monkeypatch.setattr(
        mod.surface_settings, "telegram_manager_bot_token", None, raising=False
    )
    monkeypatch.setattr(
        mod.surface_settings, "telegram_manager_bot_username", None, raising=False
    )
    surfaces = _by_platform(await build_available_surfaces(read_connector=_catalog()))
    assert surfaces[SurfacePlatform.TELEGRAM].managed_setup_available is False


async def test_one_row_per_registry_platform(monkeypatch):
    # The endpoint is registry-driven, so a newly-registered surface (Discord)
    # appears with no builder change.
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    resp = await build_available_surfaces(read_connector=_catalog())
    platforms = [s.platform for s in resp.surfaces]
    assert set(platforms) == set(SURFACE_CONNECTOR_BINDINGS)
    assert len(platforms) == len(SURFACE_CONNECTOR_BINDINGS)


async def test_an_allocated_identity_is_never_claimed_by_the_organization(
    monkeypatch,
):
    """The bug this rule caused: one mailbox blocking an organization.

    A Slack app is one identity, so whoever holds it in an organization holds
    it. Resend's system credential is an API key over a catch-all domain and
    every surface gets its own unique address off it — so a Resend surface
    existing somewhere in the org says nothing about whether this pod may have
    one. The catalog must agree with the writer, or it offers something that
    then fails.

    **WhatsApp joined that side when its numbers became a pool.** It used to be
    the clearest case of a credential that *is* an identity, because there was
    exactly one number. Now the number is allocated per surface off a shared
    app, which is Resend's shape exactly, and exclusivity moved to the finer
    rule that was always wanted: one *number* per organisation, not one
    platform. So a WhatsApp surface existing in the org says nothing either --
    and if the pool is empty, allocation says so at 503 rather than the catalog
    pretending the platform is spoken for.
    """
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    monkeypatch.setattr(mod, "AgentSurfaceEntity", SimpleNamespace)
    holder = SimpleNamespace(pod_id=uuid4(), name="resend")

    resp = await build_available_surfaces(
        read_connector=_catalog(),
        pod_id=uuid4(),
        # A repository that reports a conflict for *every* platform, and a
        # deployment that owns a pool -- which is what makes WhatsApp's identity
        # per-surface rather than deployment-wide. See the scenario below for
        # the deployment that owns none.
        surface_repository=_claim_repository(holder, pool=True),
    )

    by_platform = _by_platform(resp)
    email_claim = by_platform[SurfacePlatform.RESEND].system_claim
    assert email_claim is not None
    assert email_claim.available is True
    assert email_claim.claimed_by_pod_id is None
    whatsapp_claim = by_platform[SurfacePlatform.WHATSAPP].system_claim
    assert whatsapp_claim is not None
    assert whatsapp_claim.available is True
    assert whatsapp_claim.claimed_by_pod_id is None
    # The same repository reports a holder for this one, and for it it counts:
    # one shared bot, and holding it is holding it.
    assert by_platform[SurfacePlatform.TELEGRAM].system_claim.available is False


async def test_email_domain_is_published_so_the_builder_can_name_an_address(
    monkeypatch,
):
    """The agent builder shows the address before the agent exists.

    Every other part of that address is derivable in the client — it comes from
    the agent's own name and the pod's. The domain is the one piece that is
    deployment configuration, so without it here the builder can only promise an
    address rather than show one.
    """
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    monkeypatch.setattr(
        mod.surface_settings, "resend_inbound_domain", "ops.lemma.work", raising=False
    )
    surfaces = _by_platform(await build_available_surfaces(read_connector=_catalog()))
    assert surfaces[SurfacePlatform.RESEND].email_domain == "ops.lemma.work"
    # Nothing else mints addresses: Gmail and Outlook read a mailbox somebody
    # else owns, and its domain is not ours to name.
    assert all(
        surface.email_domain is None
        for platform, surface in surfaces.items()
        if platform is not SurfacePlatform.RESEND
    )


async def test_no_email_domain_without_the_key_that_makes_it_work(monkeypatch):
    """A domain published with no Resend key would promise an address that never
    arrives — ``provision_email_surface`` refuses on exactly the same test."""
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: False)
    monkeypatch.setattr(
        mod.surface_settings, "resend_inbound_domain", "ops.lemma.work", raising=False
    )
    surfaces = _by_platform(await build_available_surfaces(read_connector=_catalog()))
    assert surfaces[SurfacePlatform.RESEND].email_domain is None


async def test_without_a_pool_the_one_whatsapp_number_shows_as_taken(monkeypatch):
    """The catalog has to say what the writer will do, and it stopped.

    WhatsApp's exemption from the organization-wide claim is earned by the
    surface having a number of its own, which it gets from the pool. A
    deployment that owns no pool has one number, in settings, and a second pod
    taking it is the collision the rule was always about -- so the writer
    refuses it. The catalog offered it anyway, which is the disagreement that
    turns into a 409 the person only meets after committing to the choice.

    Every deployment running today owns no pool, so this is the ordinary case
    rather than the edge one.
    """
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    monkeypatch.setattr(mod, "AgentSurfaceEntity", SimpleNamespace)
    holder = SimpleNamespace(pod_id=uuid4(), name="whatsapp")

    resp = await build_available_surfaces(
        read_connector=_catalog(),
        pod_id=uuid4(),
        surface_repository=_claim_repository(holder, pool=False),
    )

    claim = _by_platform(resp)[SurfacePlatform.WHATSAPP].system_claim
    assert claim is not None
    assert claim.available is False, (
        "the catalog offered the shared number to a second pod, which the "
        "writer then refuses with a 409"
    )
    assert claim.claimed_by_surface_name == "whatsapp"


async def test_a_pool_that_cannot_be_read_keeps_the_claim_rule(monkeypatch):
    """Unreadable inventory is not evidence that a number is waiting.

    The conflict lookup beside this one degrades towards ``available`` because
    the writer still enforces the claim. This one degrades the other way for the
    same reason: failing to read the pool must not *lift* a rule, or a database
    hiccup would silently offer the shared number to everyone.
    """
    monkeypatch.setattr(mod, "has_native_credentials", lambda p: p in _NATIVE)
    monkeypatch.setattr(mod, "AgentSurfaceEntity", SimpleNamespace)
    holder = SimpleNamespace(pod_id=uuid4(), name="whatsapp")
    repository = _claim_repository(holder)
    repository.uow.session.scalar.side_effect = OperationalError(
        "SELECT 1", {}, Exception("connection reset")
    )

    resp = await build_available_surfaces(
        read_connector=_catalog(), pod_id=uuid4(), surface_repository=repository
    )

    assert _by_platform(resp)[SurfacePlatform.WHATSAPP].system_claim.available is False
