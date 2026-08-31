"""The pod-owned agent definition."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
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
from app.modules.agent.domain.entities import (
    Agent as AgentEntity,
)
from app.modules.agent.domain.value_objects import AgentKind
from app.modules.agent.infrastructure.model_converters import (
    agent_runtime_from_json,
    coerce_toolsets,
)


class AgentModel(UUIDAuditBase):
    """Pod-owned agent definition."""

    __tablename__ = "agents"
    __table_args__ = (
        # The pod's own assistant is one row per pod whose id *is* the pod's.
        # Stating it as an equivalence rather than a convention is what makes
        # the primary key enforce "at most one": a second POD_DEFAULT row for
        # the same pod would need the same id. No separate unique index.
        CheckConstraint(
            "(kind = 'POD_DEFAULT') = (id = pod_id)",
            name="ck_agents_pod_default_is_pod_id",
        ),
        # Was `name NOT IN (...)`, forbidding the name outright while nothing
        # could hold it. Inverted rather than dropped: the name is still
        # reserved against every other agent, and now also *required* of the
        # one that is the assistant -- memory folder slugs derive from
        # `agent.name`, so a differently-named row would orphan every pod's
        # existing notes.
        CheckConstraint(
            "(kind = 'POD_DEFAULT') = (name = 'pod_default')",
            name="ck_agents_name_not_pod_default_selector",
        ),
        # Nothing the runtime reads may disagree with the constants it is
        # actually run with. `description`, `icon_url` and `agent_metadata` are
        # deliberately left free -- changing the blurb harms nothing.
        CheckConstraint(
            "kind <> 'POD_DEFAULT' OR ("
            "instruction = '' AND toolsets = '[]'::jsonb "
            "AND visibility = 'POD' AND agent_runtime IS NULL"
            ")",
            name="ck_agents_pod_default_immutable",
        ),
        CheckConstraint(
            "kind IN ('USER', 'POD_DEFAULT')",
            name="ck_agents_kind",
        ),
        UniqueConstraint("pod_id", "name", name="uq_agent_pod_name"),
        Index("ix_agent_pod_name", "pod_id", "name"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # A string with a check rather than a PG enum: adding a kind later is a
    # constraint edit, where `ALTER TYPE ... ADD VALUE` cannot run inside a
    # transaction. Matches `visibility` directly below.
    #
    # Not indexed, deliberately. Every row but one per pod is `USER`, so an
    # index on it is write cost for a scan the planner would not choose; the
    # lookup that matters -- this pod's assistant -- is `id = pod_id`, served
    # by the primary key.
    kind: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=AgentKind.USER.value,
        server_default=text("'USER'"),
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(30), default="POD", nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    agent_runtime: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    toolsets: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    input_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    agent_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    pod: Mapped[Any] = relationship("Pod", foreign_keys=[pod_id])
    owner: Mapped[Any] = relationship("User", foreign_keys=[user_id])

    def __str__(self) -> str:
        return self.name or str(self.id)

    def to_entity(self) -> AgentEntity:
        return AgentEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            user_id=self.user_id,
            name=self.name,
            kind=AgentKind(self.kind),
            description=self.description,
            icon_url=self.icon_url,
            visibility=self.visibility,
            instruction=self.instruction,
            agent_runtime=agent_runtime_from_json(self.agent_runtime),
            toolsets=coerce_toolsets(self.toolsets),
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            metadata=self.agent_metadata,
        )
