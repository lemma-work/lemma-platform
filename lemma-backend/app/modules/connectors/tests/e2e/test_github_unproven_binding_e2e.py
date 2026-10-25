"""A binding migration 0063 derived is kept, but nobody proved it.

Audit H4's backfill bound every installation exactly one organization carried
to that organization, so an installation somebody squatted on before the fix
stayed theirs. Such a binding is *unproven* (``proven_at`` NULL): it keeps
working for its holder, a proven claim from another organization -- a GitHub
admin of the installation who may install apps for their Lemma organization --
takes it over, and the holder's own next proven claim stamps it final. An
unproven claim never moves it.

A binding is seeded the way the migration leaves one; the claims run against
Postgres, and GitHub is answered at the HTTP boundary for the two journeys
that go through the setup callback and the reconciler.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationBoundElsewhereError,
    GithubInstallationNotAdministeredError,
    InstallationClaimNotPermittedError,
)
from app.modules.connectors.infrastructure.models.connector_installation import (
    ConnectorInstallation,
)
from app.modules.connectors.services.auth.github_bindings import claim_installation
from app.modules.connectors.tests.e2e.test_github_installation_binding_e2e import (  # noqa: F401
    _account_in,
    _accounts_on,
    _github_connector,
    _install_github,
    _installation_id,
    _org_of_someone_else,
    _setup_callback,
    github,  # fixture
    github_sees,  # fixture
)
from app.modules.test_support.e2e_authz import auth_headers, invite_org_member

pytestmark = pytest.mark.e2e


async def _derived_binding(
    db_session: AsyncSession, installation_id: str, organization_id: str, *, proven
) -> None:
    """A binding as 0063's backfill writes one (``proven=False``), or as a
    proven claim leaves one."""
    from sqlalchemy import func

    db_session.add(
        ConnectorInstallation(
            connector_id="github",
            external_id=installation_id,
            organization_id=UUID(organization_id),
            proven_at=func.now() if proven else None,
        )
    )
    await db_session.commit()


async def _binding(db_session: AsyncSession, installation_id: str):
    db_session.expire_all()
    return (
        await db_session.execute(
            select(
                ConnectorInstallation.organization_id,
                ConnectorInstallation.proven_at,
                ConnectorInstallation.bound_by_user_id,
            ).where(
                ConnectorInstallation.connector_id == "github",
                ConnectorInstallation.external_id == installation_id,
            )
        )
    ).one()


async def _squatter_and_claimant(scenario):
    """The squatting organization, and the claiming one (the scenario's), each
    with an owner who may install apps for it."""
    _, _, squatter = await _org_of_someone_else(scenario, "h4-squatter")
    await scenario.create_org(name_prefix="H4 claimant")
    return squatter, scenario.owner_user, scenario.owner_client, scenario.org_id


async def test_a_proven_claim_from_another_org_takes_an_unproven_binding_over(
    scenario, db_session
):
    installation_id = _installation_id()
    squatter, claimant, _, org_id = await _squatter_and_claimant(scenario)
    await _derived_binding(db_session, installation_id, squatter, proven=False)

    await claim_installation(
        db_session,
        installation_id=installation_id,
        organization_id=UUID(org_id),
        user_id=UUID(claimant["id"]),
        administered=True,
    )
    await db_session.commit()

    owner, proven_at, bound_by = await _binding(db_session, installation_id)
    assert owner == UUID(org_id)
    assert proven_at is not None
    assert bound_by == UUID(claimant["id"])


async def test_a_proven_binding_is_not_taken_over(scenario, db_session):
    installation_id = _installation_id()
    holder, claimant, _, org_id = await _squatter_and_claimant(scenario)
    await _derived_binding(db_session, installation_id, holder, proven=True)

    with pytest.raises(GithubInstallationBoundElsewhereError):
        await claim_installation(
            db_session,
            installation_id=installation_id,
            organization_id=UUID(org_id),
            user_id=UUID(claimant["id"]),
            administered=True,
        )
    await db_session.rollback()

    owner, _, _ = await _binding(db_session, installation_id)
    assert owner == UUID(holder)


async def test_the_holders_proven_claim_stamps_its_binding(scenario, db_session):
    """Its unproven claim changes nothing -- the binding already works for it
    -- and its proven one makes the binding final."""
    installation_id = _installation_id()
    await scenario.create_org(name_prefix="H4 holder")
    holder, owner_id = scenario.org_id, scenario.owner_user["id"]
    await _derived_binding(db_session, installation_id, holder, proven=False)

    for administered in (False, True):
        await claim_installation(
            db_session,
            installation_id=installation_id,
            organization_id=UUID(holder),
            user_id=UUID(owner_id),
            administered=administered,
        )
        await db_session.commit()
        owner, proven_at, _ = await _binding(db_session, installation_id)
        assert owner == UUID(holder)
        assert (proven_at is not None) is administered


async def test_an_unproven_claim_never_takes_an_unproven_binding_over(
    scenario, db_session
):
    """Neither GitHub's word missing (a person who only sees it) nor Lemma's
    permission missing (a GitHub admin who is a plain member) moves it."""
    installation_id = _installation_id()
    squatter, claimant, owner_client, org_id = await _squatter_and_claimant(scenario)
    await _derived_binding(db_session, installation_id, squatter, proven=False)
    member = await scenario.create_user("h4-plain-member")
    await invite_org_member(
        owner_client, scenario.async_client, org_id=org_id, user=member
    )

    attempts = (
        (claimant["id"], False, GithubInstallationNotAdministeredError),
        (member["id"], True, InstallationClaimNotPermittedError),
    )
    for user_id, administered, refusal in attempts:
        with pytest.raises(refusal):
            await claim_installation(
                db_session,
                installation_id=installation_id,
                organization_id=UUID(org_id),
                user_id=UUID(user_id),
                administered=administered,
            )
        await db_session.rollback()

    owner, proven_at, _ = await _binding(db_session, installation_id)
    assert owner == UUID(squatter)
    assert proven_at is None


async def test_a_github_admin_takes_a_squatted_installation_back_through_setup(
    scenario,
    db_session,
    github_sees,  # noqa: F811
):
    """The journey the takeover is for: the installation's real admin comes
    back from GitHub's install page naming it, and their organization holds
    it from then on -- the squatter's accounts mint nothing for it."""
    installation_id = _installation_id()
    squatter, _, _, org_id = await _squatter_and_claimant(scenario)
    await _derived_binding(db_session, installation_id, squatter, proven=False)
    await _github_connector(db_session)
    auth_config_id = await _install_github(scenario.owner_client, org_id)
    github_sees(installation_id)

    claimed = await _setup_callback(
        scenario,
        db_session,
        org_id=org_id,
        auth_config_id=auth_config_id,
        headers=auth_headers(scenario.owner_user),
        installation_id=installation_id,
    )

    assert claimed.status_code == 200, claimed.text
    owner, proven_at, _ = await _binding(db_session, installation_id)
    assert (owner, proven_at is not None) == (UUID(org_id), True)
    [account] = await _accounts_on(db_session, org_id)
    assert account.external_ref == installation_id


async def test_a_github_admin_who_may_not_install_apps_is_never_told_ready(
    scenario,
    db_session,
    github,  # noqa: F811
):
    """Review of #1039: the reconciler cached READY before the claim could
    refuse a plain Lemma member, so the person was told READY while nothing
    was bound and no token could be minted. It waits on someone who manages
    the organization's installations -- on the refresh and on the cached read
    after it."""
    installation_id = _installation_id()
    github.person("solo")
    github.install(installation_id, on="solo", account_type="User")
    github.users["gho_solo"].sees.add(installation_id)
    await scenario.create_org(name_prefix="H4 reconcile member")
    await _github_connector(db_session)
    member = await scenario.create_user("h4-reconcile-member")
    await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=member,
    )
    account_id = await _account_in(
        db_session, org_id=scenario.org_id, user_id=member["id"], token="gho_solo"
    )
    url = (
        f"/organizations/{scenario.org_id}/connectors/accounts/{account_id}"
        "/github/installations"
    )

    for params in ({"refresh": True}, {}):
        seen = await scenario.async_client.get(
            url, headers=auth_headers(member), params=params
        )
        assert seen.status_code == 200, seen.text
        assert seen.json()["install_state"] == "PENDING_APPROVAL", seen.json()

    db_session.expire_all()
    assert (
        await db_session.scalar(
            select(ConnectorInstallation.id).where(
                ConnectorInstallation.external_id == installation_id
            )
        )
        is None
    )
    [account] = await _accounts_on(db_session, scenario.org_id)
    assert account.external_ref is None
