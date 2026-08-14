"""Function database models."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionStatus,
    FunctionRunStatus,
    FunctionType,
)


class FunctionModel(UUIDAuditBase):
    """Database model for functions."""

    __tablename__ = "functions"
    __table_args__ = (
        UniqueConstraint("pod_id", "name", name="uq_function_pod_name"),
        Index("ix_function_name", "name"),
        Index("ix_function_pod_name", "pod_id", "name"),
    )

    pod_id: Mapped[UUID] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    config_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    code_path: Mapped[str | None] = mapped_column(String, nullable=True)
    revision_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    type: Mapped[FunctionType] = mapped_column(String, default=FunctionType.API)
    status: Mapped[FunctionStatus] = mapped_column(String, default=FunctionStatus.DRAFT)
    visibility: Mapped[str] = mapped_column(String(30), default="POD", nullable=False)
    def __str__(self) -> str:
        return self.name or str(self.id)

    # Relationships. Deliberately lazy: `selectin` here meant that loading any
    # single run eagerly loaded every run that function had ever had -- payloads
    # and logs included -- on the once-a-minute reconcile cron and on every
    # terminal write. `passive_deletes` keeps delete-orphan from re-introducing
    # that load: the FK's ON DELETE CASCADE already removes the rows, and
    # without this the cascade would emit a lazy load inside an async session.
    runs: Mapped[list["FunctionRunModel"]] = relationship(
        "FunctionRunModel",
        back_populates="function",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_entity(self) -> FunctionEntity:
        entity_data = self.__dict__.copy()
        return FunctionEntity.model_validate(entity_data)


class FunctionRunModel(UUIDAuditBase):
    """Database model for function runs."""

    __tablename__ = "function_runs"

    function_id: Mapped[UUID] = mapped_column(
        ForeignKey("functions.id", ondelete="CASCADE")
    )
    revision_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    input_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[FunctionRunStatus] = mapped_column(
        String, default=FunctionRunStatus.PENDING
    )
    user_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships. Lazy for the same reason as ``FunctionModel.runs``: eager
    # loading here walked back up to the function and then down into its whole
    # run history. Every caller that needs both already joins explicitly --
    # see ``FunctionRunExecutionRepository._run_and_function``.
    function: Mapped["FunctionModel"] = relationship(
        "FunctionModel", back_populates="runs"
    )

    __table_args__ = (
        # The FK has no index of its own -- Postgres does not create one -- so
        # both the run listing and the functions ON DELETE CASCADE scanned the
        # whole table. Descending id matches ``list_runs_by_function``'s order.
        Index(
            "ix_function_runs_function_id",
            "function_id",
            text("id DESC"),
        ),
        # ``fail_expired``: the once-a-minute deadline sweep. Partial because
        # terminal runs are the overwhelming majority of the table and none of
        # them can expire, and ordered to match the query's ORDER BY so the
        # LIMIT is satisfied from the index instead of a sort.
        Index(
            "ix_function_runs_expiring",
            "deadline_at",
            "id",
            postgresql_where=text(
                "deadline_at IS NOT NULL AND status IN ('PENDING', 'RUNNING')"
            ),
        ),
        # ``list_pending_async_runs``: the recovery half of the same cron.
        Index(
            "ix_function_runs_pending_async",
            "created_at",
            "id",
            postgresql_where=text(
                "status = 'PENDING' AND job_id IS NOT NULL "
                "AND deadline_at IS NOT NULL"
            ),
        ),
    )

    def to_entity(self) -> FunctionRunEntity:
        return FunctionRunEntity.model_validate(self)
