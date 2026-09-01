"""Giving a surface its inbound address — the one place that does it.

There were three ways in, and they disagreed. Agent creation minted the readable
``{agent}.{pod}@domain`` with insert-and-retry; notification delivery minted
``pod-{hex}@domain`` through a different function; and anything arriving through
the API or a bundle import got the ``pod-{hex}`` form by default, from a
fallback inside ``create_surface`` that existed precisely so a caller need not
know about any of this. So the address a person saw depended on which door their
surface came through, only one of those doors produced something typeable, and
two of them skipped ``RESERVED_LOCAL_PARTS`` entirely — which is how a pod named
"Postmaster" could have taken ``postmaster@`` on a shared domain.

Every caller now comes here:

- ``create_agent`` and ``create_pod`` provision eagerly, so an address exists
  before anyone can write to the agent or the pod. Nobody can email something
  whose mailbox is conditional on it having sent a message first.
- Notification delivery provisions lazily, for an agent that turns out to have
  no way to reach anyone — one that predates per-agent mailboxes.
- The surfaces API and the bundle applier come through
  :func:`create_surface_on_minted_address`, which is the same allocation with
  the caller's own name, config and credentials.

``create_surface`` no longer has a fallback to reach for: a Resend surface
without an address is now an error, so a fourth door cannot open quietly.

``agent_id=None`` is the pod assistant. That is not "unset": a surface with no
agent of its own is exactly what ``surfaces_for_agent`` looks for on its behalf,
so the pod's own mailbox is what the assistant sends from.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from httpx import HTTPError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceAlreadyExistsError,
    AgentSurfaceError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    has_native_credentials,
)
from app.modules.agent_surfaces.services.email_address_allocation import (
    candidate_addresses,
    slugify,
)
from app.modules.agent_surfaces.services.pod_name_lookup import pod_name_for

logger = get_logger(__name__)


def email_is_configured() -> bool:
    """Whether this deployment can mint a working mailbox at all.

    The same key-and-domain test the surfaces catalog uses to decide whether to
    offer SYSTEM mode, so what the UI offers and what delivery actually does
    cannot drift apart. It replaced a separate on/off setting, which could be —
    and on dev was — true in one process and false in another.
    """
    return has_native_credentials(SurfacePlatform.RESEND)


ASSISTANT_SURFACE_NAME = "resend-assistant"


def surface_name_for(agent_name: str | None) -> str:
    """The pod-unique name for a mailbox, derived from whose mailbox it is.

    Every agent gets ``resend-{agent}``. The pod assistant used to get the bare
    ``resend``, which is also what ``create_surface`` picks for an unnamed
    surface of this platform — so from the moment a pod's assistant was
    provisioned at creation, the name a plain "connect email" request resolves
    to was already taken, and the request came back 409. It gets
    ``resend-assistant`` instead, leaving the platform default free.

    Forward-only. Assistants provisioned before this keep ``resend``, and
    nothing re-mints: the name is the public API identity
    (``/pods/{id}/surfaces/{surface_name}``), declared immutable, and used as
    the export directory for a pod bundle. Nothing routes on it either —
    inbound matches on the address alone — so the two spellings coexist without
    consequence, and :func:`create_surface_on_minted_address` finds either by
    agent binding rather than by name.
    """
    return f"resend-{slugify(agent_name)}" if agent_name else ASSISTANT_SURFACE_NAME


async def _insert_on_first_free_address(
    service,
    session,
    *,
    pod_id: UUID,
    agent_id: UUID,
    agent_name: str | None,
    pod_name: str | None,
    name: str | None,
    config: SurfaceConfig,
    credential_mode: SurfaceCredentialMode | None,
    account_id: UUID | None = None,
    ctx: Any = None,
) -> AgentSurfaceEntity | None:
    """Insert on the first candidate the database will accept.

    ``None`` when every candidate was taken. Mail-system failures — a refused
    domain, Resend unreachable — are raised rather than swallowed, because the
    two callers want opposite things from them and only they can decide.

    Insert and retry rather than check-then-insert: the unique index on
    ``surface_identity_email`` is the arbiter, and a pre-check would still race.

    A candidate is an address *and* a name, sharing one suffix. Both are
    pod-unique and either can be the thing that is taken, so holding the name
    fixed while varying the address meant a name clash retried the identical
    name five times and then reported "every candidate address was already
    taken" — naming the one constraint that was not the problem. The suffix
    that disambiguates `acme-p7k3@` disambiguates `resend-assistant-p7k3`
    alongside it.

    Each attempt gets a savepoint. This runs inside the caller's transaction,
    and a unique violation aborts a Postgres transaction outright — so without
    one, the second attempt raises PendingRollbackError, the commit fails, and
    *creating an agent* returns 500 because two of them wanted the same address.
    Same reasoning, and the same shape, as `notification_repository.create`.
    """
    domain = surface_settings.resend_inbound_domain or ""
    addresses = candidate_addresses(
        agent_name=agent_name, pod_name=pod_name, domain=domain
    )

    for attempt, address in enumerate(addresses):
        try:
            async with session.begin_nested():
                surface = await service.create_surface(
                    pod_id=pod_id,
                    platform=SurfacePlatform.RESEND,
                    agent_id=agent_id,
                    name=_name_for_attempt(name, address=address, attempt=attempt),
                    config=config,
                    credential_mode=credential_mode,
                    account_id=account_id,
                    surface_identity_email=address,
                    ctx=ctx,
                )
            if attempt:
                # Provisioning succeeded, on a worse address than intended: the
                # readable form was already held. Most often by this pod's own
                # assistant or by the agent's own mailbox from creation; on a
                # deployment-wide namespace it can also be another
                # organization's pod. Nothing else says so — creation returns
                # 201 and the UI prints whichever address exists — so the first
                # person to notice was the one asked to type `acme-p7k3@`.
                logger.warning(
                    "agent_surfaces.email_surface_provisioning.address_taken.degraded",
                    pod_id=str(pod_id),
                    agent_id=str(agent_id) if agent_id else None,
                    attempt=attempt,
                )
            return surface
        except IntegrityError, AgentSurfaceAlreadyExistsError:
            # The address or the surface name is taken. `create_surface` checks
            # the name up front and raises before inserting, so that arrives as
            # AgentSurfaceAlreadyExistsError rather than as the IntegrityError
            # the index would have produced; both mean "try the next
            # candidate", and the savepoint leaves the outer transaction usable.
            continue

    logger.warning(
        "agent_surfaces.email_surface_provisioning.address_unavailable.degraded",
        pod_id=str(pod_id),
        agent_id=str(agent_id) if agent_id else None,
    )
    return None


def _name_for_attempt(name: str, *, address: str, attempt: int) -> str:
    """The candidate name matching a candidate address.

    The first attempt uses the name as given. Later ones borrow the suffix the
    address already carries, so the two stay legible as a pair rather than
    drifting onto unrelated random tails.
    """
    if not attempt:
        return name
    local_part = address.split("@", 1)[0]
    suffix = local_part.rsplit("-", 1)[-1]
    return f"{name}-{suffix}"


async def provision_email_surface(
    service,
    session,
    *,
    pod_id: UUID,
    agent_id: UUID,
    agent_name: str | None,
    pod_name: str | None,
) -> tuple[AgentSurfaceEntity | None, str | None]:
    """``(surface, failure)`` — the failure is a short, safe cause for a None.

    The cause is returned rather than only logged because the log pipeline
    strips ``error`` fields, so a caller that wants to *tell somebody* why has
    no other way to find out.

    Best-effort by construction. Creating an agent or a pod must not fail
    because a mail domain is unset, and a notification that cannot be delivered
    is already a handled outcome — the row exists and the inbox has it. What
    must *not* happen is a silent success where the surface exists with an
    address nobody can receive on, which is why an unconfigured deployment
    returns None rather than inventing a domain.
    """
    if not email_is_configured():
        return None, "not_configured"

    if not surface_settings.resend_inbound_domain:  # pragma: no cover
        return None, "not_configured"

    try:
        surface = await _insert_on_first_free_address(
            service,
            session,
            pod_id=pod_id,
            agent_id=agent_id,
            agent_name=agent_name,
            pod_name=pod_name,
            name=surface_name_for(agent_name),
            config=SurfaceConfig(),
            credential_mode=SurfaceCredentialMode.SYSTEM,
        )
    except (AgentSurfaceError, HTTPError, OSError, SQLAlchemyError) as exc:
        # Deliberately not a bare ``Exception``. These are the mail system or
        # the database saying no — a refused domain, Resend unreachable, a name
        # already taken, a savepoint that cannot open on a transaction another
        # statement already invalidated. A ``TypeError`` from our own code is
        # not that, and swallowing it would turn a bug into agents that quietly
        # have no mailbox for as long as nobody looks.
        #
        # ``SQLAlchemyError`` is here because this runs inside pod and agent
        # creation. Only ``IntegrityError`` is handled by the retry loop; every
        # other database failure used to escape and take the whole creation
        # with it, which is the opposite of the best-effort contract this
        # function documents. A pod is worth having without a mailbox.
        #
        # Type and code, never ``error=str(exc)``: the log pipeline strips
        # any field named ``error`` outright, because exception text can
        # carry keys and personal data. So the one line that said why
        # provisioning failed arrived in production with the cause removed,
        # and diagnosing it needed a database. These two are bounded,
        # non-secret, and enough to name the branch that refused.
        logger.warning(
            "agent_surfaces.email_surface_provisioning.failed.degraded",
            pod_id=str(pod_id),
            agent_id=str(agent_id) if agent_id else None,
            failure_type=type(exc).__name__,
            failure_code=str(getattr(exc, "code", "") or "") or None,
        )
        return None, _cause_of(exc)

    if surface is None:
        return None, "every candidate address and name was already taken"
    return surface, None


async def create_surface_on_minted_address(
    service,
    uow,
    *,
    pod_id: UUID,
    agent_id: UUID,
    agent_name: str | None,
    platform: SurfacePlatform,
    name: str | None,
    config: SurfaceConfig,
    credential_mode: SurfaceCredentialMode | None,
    account_id: UUID | None = None,
    ctx: Any = None,
) -> AgentSurfaceEntity:
    """``create_surface``, with a readable inbound address when it needs one.

    For the callers a person drives — the surfaces API and the bundle applier —
    which carry their own name, config and credentials and so cannot use
    :func:`provision_email_surface`. Every other platform passes straight
    through, so a caller does not have to know which ones are email.

    Raises rather than degrading. These callers are answering a request that
    said "connect this surface": handing back a surface with an address nobody
    can receive on would be a worse answer than a refusal.

    **An unnamed Resend request adopts the mailbox that already exists.** A pod
    holds one mailbox per agent, and both already exist before anyone asks: the
    assistant's from pod creation, an agent's from agent creation. So "connect
    email", which is what an unnamed request is, is a request to *use* that
    address rather than to mint a second one — which is what the UI has always
    said it does ("Every agent already has one... reads as 'back on', not
    'new'"). Minting instead produced a second surface on a suffixed address,
    and left whoever asked with the ugly one.

    Only when no name is given. A caller that names a surface is naming a
    distinct thing, and the bundle applier always does — its upsert is keyed on
    that name so a bundle round-trips. Adopting under a name the caller chose
    would silently rewrite the wrong surface.

    Takes the unit of work rather than its session because the minting branch
    needs both halves of it — a savepoint per attempt, and the pod's name for the
    readable part of the address — while every other platform needs neither.
    """
    if platform is not SurfacePlatform.RESEND:
        return await service.create_surface(
            pod_id=pod_id,
            agent_id=agent_id,
            platform=platform,
            name=name,
            config=config,
            credential_mode=credential_mode,
            account_id=account_id,
            ctx=ctx,
        )

    # The inbound domain alone, deliberately not ``email_is_configured()``.
    # Minting an address needs somewhere to mint it; whether *Lemma* holds a
    # Resend key is a question about credentials, and this caller may be
    # bringing a connected account that has its own. Gating on both turned a
    # surface authenticating with its own key into a refusal — something the
    # domain-only fallback this replaced never did.
    if not surface_settings.resend_inbound_domain:
        raise AgentSurfaceValidationError(
            "Email is not configured for this deployment: set "
            "RESEND_INBOUND_DOMAIN to a verified catch-all domain."
        )

    if name is None:
        existing = await service.resend_surface_for_agent(
            pod_id=pod_id, agent_id=agent_id
        )
        if existing is not None:
            # Found by agent binding, not by name, so this holds for an
            # assistant provisioned before the name changed and still called
            # `resend`. The address is deliberately not touched: it is
            # published, and `update_surface` cannot change it anyway.
            return await service.update_surface(
                surface_id=existing.id,
                config=config,
                credential_mode=credential_mode,
                account_id=account_id,
                ctx=ctx,
            )

    surface = await _insert_on_first_free_address(
        service,
        uow.session,
        pod_id=pod_id,
        agent_id=agent_id,
        agent_name=agent_name,
        pod_name=await pod_name_for(uow, pod_id),
        # `create_surface` would default this to the platform — "resend" —
        # which is what an assistant provisioned before the rename still holds.
        # `surface_name_for` is the same name the eager and lazy paths pick, so
        # all three agree, and the retry loop suffixes it if it is taken.
        name=name or surface_name_for(agent_name),
        config=config,
        credential_mode=credential_mode,
        account_id=account_id,
        ctx=ctx,
    )
    if surface is None:
        raise AgentSurfaceValidationError(
            "Could not find a free inbound address for this surface. Renaming "
            "the pod or the agent will free one up."
        )
    return surface


def _cause_of(exc: Exception) -> str:
    """A short cause safe to show a person.

    The exception's own message is deliberately not used: it is free text from a
    provider or the database and can carry a key or somebody's address.
    """
    code = str(getattr(exc, "code", "") or "")
    return code or type(exc).__name__


__all__ = [
    "create_surface_on_minted_address",
    "email_is_configured",
    "provision_email_surface",
    "surface_name_for",
]
