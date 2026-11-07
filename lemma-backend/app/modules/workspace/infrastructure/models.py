"""Persistence models for sandboxes and their provider instances."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Integer,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxDesiredState,
    SandboxInstance,
    SandboxInstanceState,
    SandboxKind,
    SandboxMount,
    SandboxOwnerKind,
)


class SandboxModel(UUIDAuditBase):
    """A named sandbox: stable identity, independent of any running container.

    ``owner_id`` is a user id for workspaces and a pod id for function
    runtimes, so it carries no foreign key -- the two targets are different
    tables. Deletion is handled by the owning module rather than by cascade.
    """

    __tablename__ = "sandboxes"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('workspace', 'function')",
            name="ck_sandboxes_kind",
        ),
        CheckConstraint(
            "owner_kind IN ('user', 'pod')",
            name="ck_sandboxes_owner_kind",
        ),
        CheckConstraint(
            "desired_state IN ('present', 'released', 'deleted')",
            name="ck_sandboxes_desired_state",
        ),
        UniqueConstraint(
            "kind",
            "owner_kind",
            "owner_id",
            "slug",
            name="uq_sandboxes_owner_slug",
        ),
        Index(
            "ix_sandboxes_sweep",
            "desired_state",
            "last_used_at",
            "delete_after",
        ),
        CheckConstraint(
            "visibility IN ('private', 'pod', 'restricted')",
            name="ck_sandboxes_visibility",
        ),
        CheckConstraint(
            "billing_scope IN ('user', 'org')",
            name="ck_sandboxes_billing_scope",
        ),
        CheckConstraint("run_slots >= 1", name="ck_sandboxes_run_slots"),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_name: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    desired_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'present'")
    )
    epoch: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    storage_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    # NULL until the first ensure adopts a pre-consolidation volume by label or
    # creates a fresh one. Never derived from any id: the legacy name embeds a
    # random token that no longer exists anywhere else.
    provider_volume_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mounts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delete_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # What a machine is, as its owner chose it. A function sandbox keeps the
    # defaults and never reads them. `size` is what the row was last built at,
    # which is how the 2x4 migration finds the machines it has not moved yet.
    template: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'workspace'")
    )
    size: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'1x2'")
    )
    # The fabric it was last provisioned on; NULL until the first provision.
    fabric: Mapped[str | None] = mapped_column(String(16), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'private'")
    )
    billing_scope: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'user'")
    )
    # A connected machine's link; the table that owns it arrives with the link.
    connection_id: Mapped[UUID | None] = mapped_column(nullable=True)
    run_slots: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)

    def to_entity(self) -> Sandbox:
        return Sandbox(
            id=self.id,
            kind=SandboxKind(self.kind),
            owner_kind=SandboxOwnerKind(self.owner_kind),
            owner_id=self.owner_id,
            slug=self.slug,
            display_name=self.display_name,
            profile_name=self.profile_name,
            profile_digest=self.profile_digest,
            desired_state=SandboxDesiredState(self.desired_state),
            epoch=self.epoch,
            storage_generation=self.storage_generation,
            provider_volume_id=self.provider_volume_id,
            mounts=tuple(
                SandboxMount(
                    host_path=entry["host_path"],
                    container_path=entry["container_path"],
                    read_only=bool(entry.get("read_only", False)),
                )
                for entry in (self.mounts or ())
            ),
            last_used_at=self.last_used_at,
            delete_after=self.delete_after,
            template=self.template,
            size=self.size,
            fabric=self.fabric,
            machine_capabilities=dict(self.capabilities or {}),
            visibility=self.visibility,
            billing_scope=self.billing_scope,
            connection_id=self.connection_id,
            run_slots=self.run_slots,
            created_by=self.created_by,
        )


class SandboxInstanceModel(UUIDAuditBase):
    """One concrete provider object backing a sandbox at a given epoch.

    Kept as a row rather than columns on the sandbox so a destroy of the old
    container can be driven to completion while a new epoch is already running.
    """

    __tablename__ = "sandbox_instances"
    __table_args__ = (
        CheckConstraint(
            "state IN ('creating', 'ready', 'released', 'destroyed', 'error')",
            name="ck_sandbox_instances_state",
        ),
        UniqueConstraint(
            "sandbox_id", "epoch", name="uq_sandbox_instances_sandbox_epoch"
        ),
        Index("ix_sandbox_instances_live", "state", "provider"),
    )

    sandbox_id: Mapped[UUID] = mapped_column(
        ForeignKey("sandboxes.id", ondelete="CASCADE"), nullable=False
    )
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_volume_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> SandboxInstance:
        return SandboxInstance(
            id=self.id,
            sandbox_id=self.sandbox_id,
            epoch=self.epoch,
            provider=self.provider,
            state=SandboxInstanceState(self.state),
            provider_id=self.provider_id,
            provider_volume_id=self.provider_volume_id,
            last_error=self.last_error,
            ready_at=self.ready_at,
            released_at=self.released_at,
            created_at=self.created_at,
        )


# Imported here so every place that registers this module's tables by importing
# `models` -- Alembic's env, the e2e schema builder -- gets the machine tables too.
from app.modules.workspace.infrastructure.machine_models import (  # noqa: E402
    AppMachineConsentModel,
    MachineConnectionModel,
    MachineLoginBackupModel,
    MachinePreviewModel,
    MachineSessionModel,
)

__all__ = [
    "AppMachineConsentModel",
    "MachineConnectionModel",
    "MachineLoginBackupModel",
    "MachinePreviewModel",
    "MachineSessionModel",
    "SandboxInstanceModel",
    "SandboxModel",
]
