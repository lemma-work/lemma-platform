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
thing. So this entity is inventory, not an allocation. Several organisations may
hold one number at the same time -- routing narrows on the sender first and uses
the number only as a further predicate -- so "held" is never exclusive
deployment-wide, only within an organisation.

Nor is there anything saying what a number is *for*. Every row is a number this
deployment owns and sends from, and they behave identically; the only question
the inventory answers about a row is whether allocation may still offer it,
which is ``status``.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.domain.entity import Entity


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
    with it. Here ``status`` is held to its members by a CHECK constraint in the
    database, so an unknown value is not a configuration somebody chose and
    outlived; it is corruption or a migration that was not run, and
    ``ValueError`` from the enum is the right, loud answer. If a member is ever
    retired, that reasoning changes and this is where the tolerance belongs.
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
    status: WhatsAppNumberStatus = WhatsAppNumberStatus.AVAILABLE
    #: Free text for whoever runs the pool: where the number came from, what it
    #: is reserved for, why it was retired.
    notes: str | None = None

    @property
    def is_allocatable(self) -> bool:
        """Free to be handed to an organisation that does not already hold it.

        Only ``status``, because there is nothing else to ask. A number that is
        not retired may be offered to any organisation not already holding it,
        including one already held elsewhere -- several organisations holding
        one number is the design, not a collision.
        """
        return self.status is WhatsAppNumberStatus.AVAILABLE

    def credential_overrides(self) -> dict[str, str]:
        """What this number answers with, for laying over the settings defaults.

        Only the fields it actually has. A `None` here means "this number does
        not say", and the deployment-wide setting is the answer -- so omitting
        the key is the whole mechanism by which a pool of one, or a pool whose
        numbers share an app, needs no rows and no duplicated secrets.

        Returning `""` for an absent value instead would be the bug this shape
        exists to avoid: it would override the setting with nothing, and the
        send would fail on an empty token that looks configured.
        """
        answers = {
            "phone_number_id": self.phone_number_id,
            "waba_id": self.waba_id,
            "access_token": self.access_token,
            "app_secret": self.app_secret,
        }
        return {key: value for key, value in answers.items() if value}
