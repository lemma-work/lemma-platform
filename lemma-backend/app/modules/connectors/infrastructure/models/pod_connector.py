"""The two pod tables that reference org installations and accounts.

Both join ``RESOURCE_TABLES`` (``core/authorization/resource_tables.py``), so
authorization hydrates their pod, owner and visibility from the row like any
other pod resource. Every foreign key cascades from the org row it points at:
deleting an account removes its pod accounts, deleting an installation removes
the pod connectors on it, and a SHARED connector whose account went away is
left with ``shared_pod_account_id`` NULL -- which is what NEEDS_ACCOUNT is.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorEntity,
    PodConnectorSource,
    PodConnectorVisibility,
)


class PodAccount(UUIDAuditBase):
    """One org account in use in one pod, owned by the account's owner."""

    __tablename__ = "pod_accounts"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    auth_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PodAccountVisibility.PERSONAL.value,
        server_default=PodAccountVisibility.PERSONAL.value,
    )
    is_member_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PodAccountSource.MANUAL.value
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The owner's "allow use inside sandboxes": whether a shared account's
    #: credential may be written into a workspace (the git bridge). Only the
    #: owner sets it, and withdrawing the account clears it.
    allow_sandbox_use: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (
        UniqueConstraint("pod_id", "account_id", name="uq_pod_accounts_pod_account"),
        # Which of a person's accounts on one installation their per-member
        # calls use in this pod: at most one.
        Index(
            "uq_pod_accounts_member_default",
            "pod_id",
            "owner_user_id",
            "auth_config_id",
            unique=True,
            postgresql_where=text("is_member_default"),
        ),
        Index("ix_pod_accounts_account", "account_id"),
        Index("ix_pod_accounts_pod_owner", "pod_id", "owner_user_id"),
    )

    def to_entity(self) -> PodAccountEntity:
        return PodAccountEntity.model_validate(self)


class PodConnector(UUIDAuditBase):
    """A pod's declaration of one org installation it uses."""

    __tablename__ = "pod_connectors"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    auth_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    shared_pod_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pod_accounts.id", ondelete="SET NULL"), nullable=True
    )
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PodConnectorVisibility.POD.value,
        server_default=PodConnectorVisibility.POD.value,
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PodConnectorSource.MANUAL.value,
        server_default=PodConnectorSource.MANUAL.value,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("pod_id", "slug", name="uq_pod_connectors_pod_slug"),
        Index("ix_pod_connectors_auth_config", "auth_config_id"),
        Index("ix_pod_connectors_shared_pod_account", "shared_pod_account_id"),
    )

    def to_entity(self) -> PodConnectorEntity:
        return PodConnectorEntity.model_validate(self)
