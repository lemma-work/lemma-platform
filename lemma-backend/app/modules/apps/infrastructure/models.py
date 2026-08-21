"""App database models."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
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
    status: Mapped[AppStatus] = mapped_column(
        String, nullable=False, default=AppStatus.DRAFT
    )
    # Apps default to PUBLIC, unlike every other resource. An app is a shell --
    # HTML and JS -- whose data calls are authorized on their own; the SDK's
    # AppGate turns a denial into a sign-in or request-access screen. Defaulting
    # to POD made that shell unreachable on its own public host, so a deployed
    # app 404'd until someone found the share dialog.
    visibility: Mapped[str] = mapped_column(
        String(30), default="PUBLIC", nullable=False
    )

    def to_entity(self) -> AppEntity:
        return AppEntity.model_validate(self)


class AppReleaseModel(UUIDCreatedBase):
    __tablename__ = "app_releases"
    __table_args__ = (
        UniqueConstraint("app_id", "version", name="uq_app_release_version"),
        Index("ix_app_release_app_id", "app_id"),
    )

    app_id: Mapped[UUID] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String, nullable=False)
    dist_root_path: Mapped[str] = mapped_column(String, nullable=False)
    dist_archive_path: Mapped[str | None] = mapped_column(String, nullable=True)

    def to_entity(self) -> AppReleaseEntity:
        return AppReleaseEntity.model_validate(self)
