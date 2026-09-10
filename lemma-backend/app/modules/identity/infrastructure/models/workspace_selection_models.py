from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class WorkspaceSelection(Base):
    __tablename__ = "identity_workspace_selections"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    pod_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pods.id", ondelete="SET NULL")
    )
