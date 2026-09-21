"""The pool table: every WhatsApp number this deployment owns.

Mirrors ``migrations/versions/2026-09-21_whatsapp_number_pool_0039.py`` column
for column, constraint for constraint, index name for index name. That is not
tidiness: a schema built from metadata must carry the same guarantees as one
built by the migration, and anything declared in only one of the two is
something autogenerate would offer to DROP.

Kept in its own module rather than added to ``models.py`` because it is the
inventory, not a surface -- and because ``models.py`` is already close to the
600-line ceiling the architecture gate enforces.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
    WhatsAppNumberStatus,
)


class WhatsAppNumber(UUIDAuditBase):
    """One number the deployment owns.

    No column records who holds it. The holder is the surface --
    ``agent_surfaces.organization_id`` plus ``surface_identity_id``, unique
    together -- so there is exactly one place that can be wrong about who holds
    a scarce thing.
    """

    __tablename__ = "surface_whatsapp_numbers"
    __table_args__ = (
        # The enum lives in the database as well as in the domain, and that is
        # what lets `to_entity` refuse an unknown value loudly instead of
        # tolerating it: a row cannot hold one.
        CheckConstraint(
            "status IN ('AVAILABLE', 'RETIRED')", name="ck_whatsapp_number_status"
        ),
        # Allocation's whole predicate on this table -- "an AVAILABLE number" --
        # and the cold-open fallback's, which wants the oldest of them. The
        # "this org does not hold it" half is answered by `agent_surfaces`, not
        # here, so it is not in this index.
        Index("ix_whatsapp_number_allocatable", "status", "created_at"),
    )

    # Unique, and no index of its own beyond that: every read starts from it --
    # the webhook path, the Graph call, the allocation retry -- and the
    # uniqueness constraint's index serves all of them.
    phone_number_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    # Unique too, and for a reason that outlives cosmetics: the same number
    # added twice under two `phone_number_id`s would be handed to two
    # organisations as if it were two numbers.
    display_phone_number: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    waba_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The three secrets. Nullable, and NULL is meaningful: it means "fall back
    # to settings", which is what lets a one-number deployment declare nothing.
    # Text rather than String(n) because the `lsenc1:` envelope is longer than
    # the value it wraps -- the same reason `agent_surfaces.webhook_secret` is.
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per Meta *app*, not per number: numbers co-tenanted under one app answer
    # with the same value, on purpose. Stored per row anyway so the column can
    # say "the secret that verifies this number's deliveries" without the
    # reader having to know the app topology.
    app_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    verify_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Flow assets are WABA-scoped, so they belong to the number.
    onboarding_email_flow_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    onboarding_code_flow_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Leading column of `ix_whatsapp_number_allocatable`, so no index of its own.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_entity(self) -> WhatsAppNumberEntity:
        return WhatsAppNumberEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            phone_number_id=self.phone_number_id,
            display_phone_number=self.display_phone_number,
            waba_id=self.waba_id,
            # Decrypt at rest; legacy plaintext rows pass through unchanged.
            access_token=get_secret_cipher().decrypt_str(self.access_token),
            app_secret=get_secret_cipher().decrypt_str(self.app_secret),
            verify_token=get_secret_cipher().decrypt_str(self.verify_token),
            onboarding_email_flow_id=self.onboarding_email_flow_id,
            onboarding_code_flow_id=self.onboarding_code_flow_id,
            status=WhatsAppNumberStatus(self.status),
            notes=self.notes,
        )
