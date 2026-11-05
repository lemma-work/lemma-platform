"""What an import plan says about a bundle's pod connectors, and the choices.

A bundled connector names a catalog connector (``gmail``) and a mode. Which of
the importing organization's installations it maps to, whether the import may
make one, and -- for a SHARED connector -- which account it runs as are not in
the bundle and cannot be: they are the importer's to choose in their own org.
The plan offers those choices (:class:`ConnectorPlan`); the apply request makes
them (:class:`ConnectorChoice`); :func:`resolve_connector_choices` checks one
against the other before anything is written.

Two outcomes are not alike. A SHARED connector nobody shares an account for
imports anyway, as NEEDS_ACCOUNT: the pod works, that connector waits for an
account, and the plan says so as a warning. A connector with no installation to
map to cannot be declared at all, and everything naming its slug would be bound
to nothing -- that blocks the apply until the importer picks or creates one.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectorPlanStatus(str, Enum):
    #: Declared on apply and usable once it is.
    READY = "READY"
    #: SHARED with no account chosen: declared, and waits for one.
    NEEDS_ACCOUNT = "NEEDS_ACCOUNT"
    #: No installation of the catalog connector in the org. Blocks the apply
    #: unless the importer chooses to create one (``can_create_installation``).
    NEEDS_INSTALLATION = "NEEDS_INSTALLATION"
    #: The pod already declares this slug for the same catalog connector in the
    #: same mode; the pod's own declaration (its installation, its account) is
    #: kept.
    EXISTS = "EXISTS"
    #: The pod already declares this slug for a different connector, or in the
    #: other mode. Blocks: keeping it would run the bundle's resources as an
    #: account they were not built for (a shared mailbox for a per-member one,
    #: or the reverse).
    CONFLICT = "CONFLICT"


class DeclaredConnector(BaseModel):
    """What the importing pod already declares under a slug."""

    connector: str
    mode: str


def declared_conflict(
    slug: str, *, connector: str, mode: str, declared: DeclaredConnector
) -> str | None:
    """Why a bundled connector cannot stand on the pod's own of the same slug.

    ``None`` when the two agree -- same catalog connector, same mode -- and the
    pod's declaration is kept. Planning and applying both ask this one
    function, so an import the plan accepted is not refused at its step for a
    reason the plan never gave, nor the other way round.
    """
    if declared.connector != connector:
        return (
            f"This pod already has a connector '{slug}' for {declared.connector}, "
            f"not {connector}. Rename or remove it, then plan again."
        )
    if declared.mode != mode:
        return (
            f"This pod already has a connector '{slug}' for {connector} that is "
            f"{_MODE_WORDS.get(declared.mode, declared.mode)}, and this bundle's "
            f"is {_MODE_WORDS.get(mode, mode)}. Change or rename the pod's, then "
            "plan again."
        )
    return None


_MODE_WORDS = {
    "PER_MEMBER": "per member (each person's own account)",
    "SHARED": "shared (one account for everyone)",
}


class InstallationOption(BaseModel):
    auth_config_id: UUID
    name: str
    is_default: bool = False


class AccountOption(BaseModel):
    """One of the importer's own accounts, shareable for a SHARED connector."""

    account_id: UUID
    auth_config_id: UUID
    label: str | None = None


class ConnectorPlan(BaseModel):
    slug: str
    connector: str
    mode: Literal["PER_MEMBER", "SHARED"]
    required: bool = False
    purpose: str | None = None
    visibility: Literal["POD", "RESTRICTED"] = "POD"
    status: ConnectorPlanStatus
    #: The org's active installations of ``connector``, default first.
    installations: list[InstallationOption] = Field(default_factory=list)
    #: The installation used unless the importer picks another: the org's
    #: default one. ``None`` when there is none.
    auth_config_id: UUID | None = None
    #: The importer may install Lemma's own app for the connector as part of
    #: the import (holds ``org.installation.manage``, and it needs no config).
    can_create_installation: bool = False
    #: SHARED only: the importer's own accounts on those installations.
    account_options: list[AccountOption] = Field(default_factory=list)
    #: ``<type>/<name>`` of the bundled resources that name this slug.
    used_by: list[str] = Field(default_factory=list)
    message: str | None = None

    @property
    def is_shared(self) -> bool:
        return self.mode == "SHARED"

    @property
    def declares(self) -> bool:
        """Whether applying the plan declares this connector at all."""
        return self.status not in (
            ConnectorPlanStatus.EXISTS,
            ConnectorPlanStatus.CONFLICT,
        )


class ConnectorChoice(BaseModel):
    """What the importer chose for one bundled connector.

    Leave it all out to take the plan's default installation and share no
    account. ``auth_config_id`` and ``create_installation`` are alternatives.
    """

    model_config = ConfigDict(extra="forbid")

    auth_config_id: UUID | None = Field(
        default=None,
        description="One of the plan's `installations` for this connector.",
    )
    create_installation: bool = Field(
        default=False,
        description=(
            "Install Lemma's own app for the connector in your organization and "
            "use it. Offered only where the plan says `can_create_installation`."
        ),
    )
    share_account_id: UUID | None = Field(
        default=None,
        description=(
            "SHARED only: one of your own accounts (the plan's "
            "`account_options`) to share with the pod for this connector to run "
            "as. Left out, the connector imports needing an account."
        ),
    )


class BlockingConnector(BaseModel):
    slug: str
    reason: str


def _resolve_one(
    plan: ConnectorPlan, choice: ConnectorChoice
) -> ConnectorChoice | BlockingConnector:
    if plan.status is ConnectorPlanStatus.CONFLICT:
        return BlockingConnector(slug=plan.slug, reason=plan.message or "conflict")
    if choice.create_installation and choice.auth_config_id is not None:
        return BlockingConnector(
            slug=plan.slug,
            reason="choose an installation or to create one, not both",
        )
    known = {option.auth_config_id for option in plan.installations}
    if choice.create_installation:
        if not plan.can_create_installation:
            return BlockingConnector(
                slug=plan.slug,
                reason=(
                    f"you cannot install {plan.connector} in this organization; "
                    "ask an organization admin to install it"
                ),
            )
        auth_config_id = None
    elif choice.auth_config_id is not None:
        if choice.auth_config_id not in known:
            return BlockingConnector(
                slug=plan.slug,
                reason=(
                    f"that is not an installation of {plan.connector} in this "
                    "organization"
                ),
            )
        auth_config_id = choice.auth_config_id
    elif plan.auth_config_id is not None:
        auth_config_id = plan.auth_config_id
    else:
        return BlockingConnector(
            slug=plan.slug,
            reason=plan.message or f"no installation of {plan.connector}",
        )
    share = choice.share_account_id
    if share is not None:
        offered = {
            option.account_id: option.auth_config_id for option in plan.account_options
        }
        if not plan.is_shared:
            return BlockingConnector(
                slug=plan.slug,
                reason="only a SHARED connector runs as a shared account",
            )
        if share not in offered or offered[share] != auth_config_id:
            return BlockingConnector(
                slug=plan.slug,
                reason=("that is not one of your accounts on the chosen installation"),
            )
    return ConnectorChoice(
        auth_config_id=auth_config_id,
        create_installation=choice.create_installation,
        share_account_id=share,
    )


def resolve_connector_choices(
    plans: list[ConnectorPlan], requested: Mapping[str, ConnectorChoice]
) -> tuple[dict[str, ConnectorChoice], list[BlockingConnector]]:
    """The importer's choices made whole, or what still blocks the apply.

    Every connector the plan declares comes back with an installation (chosen,
    defaulted, or to be created) and the account it is to share, if any. A
    choice for a slug the bundle does not declare is refused rather than
    ignored: it is a typo, and ignoring it would import something the person
    did not mean.
    """
    by_slug = {plan.slug: plan for plan in plans}
    blocking = [
        BlockingConnector(slug=slug, reason="this bundle declares no such connector")
        for slug in sorted(set(requested) - set(by_slug))
    ]
    resolved: dict[str, ConnectorChoice] = {}
    for plan in plans:
        if plan.status is ConnectorPlanStatus.EXISTS:
            continue
        outcome = _resolve_one(plan, requested.get(plan.slug) or ConnectorChoice())
        if isinstance(outcome, BlockingConnector):
            blocking.append(outcome)
        else:
            resolved[plan.slug] = outcome
    return resolved, blocking


__all__ = [
    "AccountOption",
    "BlockingConnector",
    "ConnectorChoice",
    "ConnectorPlan",
    "ConnectorPlanStatus",
    "DeclaredConnector",
    "InstallationOption",
    "declared_conflict",
    "resolve_connector_choices",
]
