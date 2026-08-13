"""App database models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase, UUIDCreatedBase
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity, AppStatus


class AppModel(UUIDAuditBase):
    __tablename__ = "apps"
    __table_args__ = (
        UniqueConstraint("pod_id", "name", name="uq_app_pod_name"),
        UniqueConstraint("public_slug", name="uq_app_public_slug"),
        Index("ix_app_name", "name"),
        Index("ix_app_pod_name", "pod_id", "name"),
        Index("ix_app_public_slug", "public_slug"),
    )

    pod_id: Mapped[UUID] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    public_slug: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_archive_path: Mapped[str | None] = mapped_column(String, nullable=True)
    current_release_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[AppStatus] = mapped_column(String, nullable=False, default=AppStatus.DRAFT)
    # Apps default to PUBLIC, unlike every other resource. An app is a shell --
    # HTML and JS -- whose data calls are authorized on their own; the SDK's
    # AppGate turns a denial into a sign-in or request-access screen. Defaulting
    # to POD made that shell unreachable on its own public host, so a deployed
    # app 404'd until someone found the share dialog.
    visibility: Mapped[str] = mapped_column(String(30), default="PUBLIC", nullable=False)

    def to_entity(self) -> AppEntity:
        return AppEntity.model_validate(self)


class AppReleaseModel(UUIDCreatedBase):
    __tablename__ = "app_releases"
    __table_args__ = (
        UniqueConstraint("app_id", "version", name="uq_app_release_version"),
        UniqueConstraint("app_id", "release_number", name="uq_app_release_number"),
        Index("ix_app_release_app_id", "app_id"),
        Index("ix_app_release_app_created", "app_id", text("created_at DESC")),
    )

    app_id: Mapped[UUID] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"))
    # The dist digest -- the release's identity, its storage key, and its ETag.
    version: Mapped[str] = mapped_column(String, nullable=False)
    # The per-app counter shown to people and used in preview hosts. A sha256 is
    # too long for a DNS label and unreadable in a list; `v7` is neither.
    release_number: Mapped[int] = mapped_column(nullable=False)
    dist_root_path: Mapped[str] = mapped_column(String, nullable=False)
    dist_archive_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # Source belongs to the release that was built from it, not to the app: with
    # one column on the app row, a rollback paired an old build with new source.
    source_archive_path: Mapped[str | None] = mapped_column(String, nullable=True)
    source_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set when retention has deleted this release's bytes. The row survives so
    # the history stays legible ("v3 -- build removed") instead of developing
    # unexplained gaps; dist_root_path records where the bytes were.
    pruned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> AppReleaseEntity:
        return AppReleaseEntity.model_validate(self)
