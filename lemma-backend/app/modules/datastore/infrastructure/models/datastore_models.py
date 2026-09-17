from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.infrastructure.db.base import UUIDAuditBase


if TYPE_CHECKING:
    from app.modules.datastore.domain.datastore_entities import DatastoreTableEntity
    from app.modules.datastore.domain.file_entities import (
        DatastoreFileEntity,
        DatastoreSignedLinkEntity,
    )


class DatastoreTable(UUIDAuditBase):
    """Datastore Table model."""

    __tablename__ = "datastore_tables"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    table_name: Mapped[str] = mapped_column(String(255))
    primary_key_column: Mapped[str] = mapped_column(String(255), default="id")
    columns: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False
    )  # Simplified type
    config: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True)
    enable_rls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    visibility: Mapped[str] = mapped_column(String(30), default="POD", nullable=False)

    __table_args__ = (
        Index(
            "ix_datastore_table_pod_name",
            "pod_id",
            "table_name",
            unique=True,
        ),
    )

    def to_entity(self) -> "DatastoreTableEntity":
        from app.modules.datastore.domain.datastore_entities import DatastoreTableEntity

        return DatastoreTableEntity(
            id=self.id,
            pod_id=self.pod_id,
            user_id=self.user_id,
            table_name=self.table_name,
            primary_key_column=self.primary_key_column,
            columns=self.columns,
            config=self.config,
            enable_rls=self.enable_rls,
            visibility=self.visibility,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class DatastoreFile(UUIDAuditBase):
    __tablename__ = "datastore_files"

    # No index=True: pod_id already leads three composites below, so a
    # single-column index on it was write cost with nothing to serve.
    pod_id: Mapped[UUID] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"))
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="FILE", nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(30), default="PERSONAL", nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    search_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    file_metadata: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    last_processing_error: Mapped[str | None] = mapped_column(
        Text, default=None, nullable=True
    )
    processing_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # Leads with status because the dispatch and recovery sweeps filter it
        # with no pod_id, and led by pod_id this index could not serve them --
        # every tick fell back to a sequential scan of the whole table.
        #
        # The predicate is what keeps it nearly free: folders are created
        # NOT_REQUIRED and non-indexable types never reach PENDING, so only
        # documents actually in flight are in here. At rest that is no rows.
        Index(
            "ix_datastore_file_status",
            "status",
            "pod_id",
            "created_at",
            postgresql_where=text("kind = 'FILE' AND search_enabled"),
        ),
        Index(
            "ix_datastore_file_pod_path",
            "pod_id",
            "path",
            unique=True,
        ),
        Index(
            "ix_datastore_file_pod_path_prefix",
            "pod_id",
            "path",
            postgresql_ops={"path": "text_pattern_ops"},
        ),
    )

    def to_entity(self) -> "DatastoreFileEntity":
        from app.modules.datastore.domain.file_entities import (
            DatastoreFileEntity,
            FileKind,
            FileStatus,
        )

        return DatastoreFileEntity(
            id=self.id,
            pod_id=self.pod_id,
            owner_user_id=self.owner_user_id,
            kind=FileKind(self.kind),
            visibility=self.visibility,
            name=self.name,
            path=self.path,
            description=self.description,
            mime_type=self.mime_type,
            size_bytes=self.size_bytes,
            search_enabled=self.search_enabled,
            status=FileStatus(self.status),
            metadata=self.file_metadata,
            indexed_at=self.indexed_at,
            last_processing_error=self.last_processing_error,
            processing_attempts=self.processing_attempts,
            content_sha256=self.content_sha256,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class DatastoreSignedLink(UUIDAuditBase):
    """A public short link (``/s/{code}``) to one datastore file.

    The link record lives here rather than only in Redis because it is a
    capability grant, not a cache: it is the whole of what stands between a URL
    and someone's file, and it now lasts up to seven days. Redis durability
    varies by deployment — the compose stack snapshots every 60 seconds with no
    append-only file, managed key-value services differ again — so a link that
    existed only there survived or vanished depending on how the operator had
    deployed, which is not a lifetime anyone can promise a recipient.

    Redis still serves every fetch; see ``services/files/signed_url.py`` for
    which half owns what, and why the spend counter deliberately stays there.
    """

    __tablename__ = "datastore_signed_links"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pod_id: Mapped[UUID] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    path: Mapped[str] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    max_hits: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exhausted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Serves the pod's own "which links are live" listing, and the sweeper
        # scans `expires_at` on its own.
        Index("ix_datastore_signed_link_pod_created", "pod_id", "created_at"),
        Index("ix_datastore_signed_link_expires_at", "expires_at"),
    )

    def to_entity(self) -> "DatastoreSignedLinkEntity":
        from app.modules.datastore.domain.file_entities import (
            DatastoreSignedLinkEntity,
        )

        return DatastoreSignedLinkEntity(
            id=self.id,
            code=self.code,
            pod_id=self.pod_id,
            created_by_user_id=self.created_by_user_id,
            path=self.path,
            object_key=self.object_key,
            content_type=self.content_type,
            filename=self.filename,
            content_sha256=self.content_sha256,
            size_bytes=self.size_bytes,
            max_hits=self.max_hits,
            expires_at=self.expires_at,
            revoked_at=self.revoked_at,
            exhausted_at=self.exhausted_at,
            created_at=self.created_at,
        )
