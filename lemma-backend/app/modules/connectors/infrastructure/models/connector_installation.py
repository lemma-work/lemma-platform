"""Which Lemma organization an installation of a Lemma-operated app belongs to.

Some connectors are one app that Lemma operates, installed into many upstream
tenants: one GitHub App serves every organization that installs it, and the
upstream names each installation by a bare id. Whoever holds that id holds
the app's power over the tenant -- GitHub App tokens on every repository in
the installation, and its webhooks (audit H4). So the upstream id cannot be a
free value on an account; a row here is the installation's owner on the
Lemma side: the first organization to bind it through a verified path, and
nobody else after. ``UNIQUE (connector_id, external_id)`` is the rule; the
binding code is only its messenger.

Not an auth config. An auth config is an organization's app *registration* --
its client id and secret, several per org, unique in no wider sense. This is
the ownership of one *upstream tenant*, unique across every organization.
GitHub binds here today; Lemma's own Slack app holds the same fact in
``slack_manager_installs`` and can move here. DDL matches migration 0063.
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
    #: nobody proved -- a proven claim from another organization takes such a
    #: binding over, and the holder's own next proven claim stamps it.
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
            name="uq_connector_installations_connector_external",
        ),
        Index("ix_connector_installations_organization_id", "organization_id"),
    )
