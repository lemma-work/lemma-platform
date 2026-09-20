"""The WhatsApp numbers a deployment owns, and which of them may be handed out.

A deployment used to have exactly one number, named in settings, and "the
WhatsApp credential" and "the WhatsApp number" were the same sentence. They are
not the same thing once there are several, so this names the number: what
addresses it (``phone_number_id``), what a person sees (``display_phone_number``)
and the per-number credentials it answers with.

Every credential field is optional and absent means *fall back to settings* --
which is what lets a one-number deployment keep working with no rows at all.
See ``migrations/versions/2026-09-21_whatsapp_number_pool_0039.py`` for why
``app_secret`` is here despite being per Meta *app* rather than per number.

Who *holds* a number is deliberately not here. The holder is the surface, on
``agent_surfaces.surface_identity_id``, scoped by ``organization_id``: a second
copy on this row would be two places that can disagree about who holds a scarce
thing. So this entity is inventory, not an allocation.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.domain.entity import Entity


class WhatsAppNumberRole(StrEnum):
    """What a number is *for*, which is not the same as whether it is free.

    Mirrors ``ck_whatsapp_number_role``.
    """

    #: The line in settings: personal pods ride it, identity's phone
    #: verification uses it. Exactly one exists (``uq_whatsapp_number_shared``)
    #: and it is never handed to an organisation.
    SHARED = "SHARED"
    #: The pool proper. One organisation at a time, several over its lifetime.
    ALLOCATABLE = "ALLOCATABLE"


class WhatsAppNumberStatus(StrEnum):
    """Whether the deployment will hand this number out again.

    Mirrors ``ck_whatsapp_number_status``. ``RETIRED`` is "stop allocating
    this", not "we gave it up" -- a number already held stays held, and the row
    stays so the holder still resolves.
    """

    AVAILABLE = "AVAILABLE"
    RETIRED = "RETIRED"


class WhatsAppNumberEntity(Entity):
    """One number in the pool.

    Note what this deliberately does *not* copy from ``AgentSurface``: the
    tolerance for a retired enum value read from the database
    (``to_entity_or_none`` and ``_log_retired_value``). That pattern exists
    because ``surface_type`` and ``event_mode`` are free ``String`` columns
    whose enums lost members, so a stored row can legitimately name something
    this code no longer knows -- and one such row must not take a whole page
    with it. Here both ``role`` and ``status`` are held to their members by
    CHECK constraints in the database, so an unknown value is not a
    configuration somebody chose and outlived; it is corruption or a migration
    that was not run, and ``ValueError`` from the enum is the right, loud
    answer. If a member is ever retired, that reasoning changes and this is
    where the tolerance belongs.
    """

    #: The routing key and the Graph API address. Opaque -- never the E.164
    #: number, which Meta mangles in a URL path.
    phone_number_id: str
    #: What a person sees as the sender, resolved from Graph when the number was
    #: added so that allocation itself never makes an API call.
    display_phone_number: str
    waba_id: str
    #: Decrypted at the repository boundary, exactly as
    #: ``AgentSurfaceEntity.webhook_secret`` is: an entity in hand holds usable
    #: values, and the envelope never leaves infrastructure.
    access_token: str | None = None
    app_secret: str | None = None
    verify_token: str | None = None
    #: WABA-scoped assets, so they belong to the number and not to the
    #: deployment.
    onboarding_email_flow_id: str | None = None
    onboarding_code_flow_id: str | None = None
    role: WhatsAppNumberRole = WhatsAppNumberRole.ALLOCATABLE
    status: WhatsAppNumberStatus = WhatsAppNumberStatus.AVAILABLE
    #: Free text for whoever runs the pool: where the number came from, what it
    #: is reserved for, why it was retired.
    notes: str | None = None

    @property
    def is_allocatable(self) -> bool:
        """Free to be handed to an organisation that does not already hold it."""
        return (
            self.role is WhatsAppNumberRole.ALLOCATABLE
            and self.status is WhatsAppNumberStatus.AVAILABLE
        )
