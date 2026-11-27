"""Which Lemma organization an installation of a Lemma-operated app belongs to.

Some connectors are one app that Lemma operates, installed into many upstream
tenants: one GitHub App serves every organization that installs it, and the
upstream names each installation by a bare id. Whoever holds that id holds
the app's power over the tenant -- GitHub App tokens on every repository in
the installation, and its webhooks (audit H4). So the upstream id cannot be a
free value on an account; a row here is the installation's owner on the
Lemma side: each organization that bound it through a verified path. GitHub
allows one installation per GitHub account, so a GitHub organization worked on
from several Lemma organizations is one installation with a row for each.
Every row is earned on its own claim (``github_bindings``); one organization's
row gives no other anything. ``UNIQUE (connector_id, external_id,
organization_id)``; migration 0080 widened it from one row per installation.

Not an auth config. An auth config is an organization's app *registration* --
its client id and secret, several per org, unique in no wider sense. This is
an organization's hold on one *upstream tenant*.
GitHub binds here today; Lemma's own Slack app holds the same fact in
``slack_manager_installs`` and can move here. DDL matches migrations 0063 and 0080.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase


class ConnectorInstallation(UUIDCreatedBase):
    __tablename__ = "connector_installations"

    #: The connector whose app this is an installation of (``github``). Not a
    #: foreign key: the catalog is seeded at runtime, after migrations.
    connector_id: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The upstream's own id for the installation: a GitHub installation id,
    #: a Slack team id.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Who bound it, for the audit trail. Not an authority: the binding is the
    #: organization's, and survives the person leaving it.
    bound_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: When a claimant last proved control of it: GitHub said they administer
    #: it, and they may install apps for the organization in Lemma. NULL for
    #: a binding migration 0063 derived from the accounts carrying it, which
    #: nobody proved; the holder's own next proven claim stamps it.
    proven_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: What a provider records about an installation beyond who owns it.
    details: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )

    __table_args__ = (
        UniqueConstraint(
            "connector_id",
            "external_id",
            "organization_id",
            name="uq_connector_installations_connector_external_org",
        ),
        Index("ix_connector_installations_organization_id", "organization_id"),
    )
