"""The conferral bound: nobody hands out access they do not themselves hold.

PS-POD-013 and PS-ACCESS-010 state one rule, and until this module existed the
codebase implemented two halves of it, both wrong.

Role *assignment* compared hierarchy ranks (``ROLE_HIERARCHY``), which knows only
the four built-in pod roles -- so every custom role scored zero and cleared any
cap. Role *authorship* compared nothing at all: ``create_or_update_role``
validated that a permission id exists, never that the author holds it. Together
they let a principal mint a role carrying permissions they do not have and hand
it to somebody else.

So the rule lives here once, as a comparison of *permission sets*, and both
paths call it. Ranks cannot express the rule -- a custom role is a set of
permissions, and only a set comparison can bound it.

Two principals are exempt, and only two:

* a superuser, who is outside the model entirely; and
* an organization owner, who already holds every authority the organization can
  express and whose pods are theirs by ``_is_org_owner_of_pod``. Bounding them
  by their pod-scoped permission set would refuse an owner who holds no pod role
  at all, which is the ordinary case.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from app.core.authorization.context import Context
from app.core.authorization.permissions import equivalent_permission_ids
from app.core.domain.errors import DomainError

#: The refusal code every conferral denial carries, so a client can tell
#: "you may not grant this" apart from "you may not be here at all".
CONFERRAL_EXCEEDS_HOLDER = "CONFERRAL_EXCEEDS_HOLDER"

#: The organization role that is exempt from the bound. Held as a name rather
#: than an import so this module stays free of the identity module.
_EXEMPT_ROLE_NAME = "ORG_OWNER"


def permissions_not_held(
    held: Collection[str],
    requested: Iterable[str],
) -> list[str]:
    """Requested permission ids that ``held`` cannot satisfy, sorted.

    Satisfaction follows ``equivalent_permission_ids``, the same relation
    ``Context.has_permission`` uses: holding ``app.delete`` satisfies a request
    to confer ``app.read``, because the holder can already do it.
    """
    holder = set(held)
    return sorted(
        {
            permission_id
            for permission_id in requested
            if not (equivalent_permission_ids(permission_id) & holder)
        }
    )


def is_exempt_from_conferral_bound(ctx: Context) -> bool:
    return ctx.is_superuser or _EXEMPT_ROLE_NAME in ctx.role_names


def assert_can_confer(
    ctx: Context,
    permission_ids: Iterable[str],
    *,
    action: str,
) -> None:
    """Refuse ``ctx`` any permission in ``permission_ids`` it does not hold.

    ``action`` completes the sentence "You may not <action> ..." and should read
    as what the caller was trying to do ("grant permissions you do not hold").
    """
    if is_exempt_from_conferral_bound(ctx):
        return
    refuse_conferral_beyond(
        held=ctx.permission_ids,
        requested=permission_ids,
        action=action,
    )


def refuse_conferral_beyond(
    *,
    held: Collection[str],
    requested: Iterable[str],
    action: str,
) -> None:
    """The bound applied to a permission set resolved by the caller.

    Separate from :func:`assert_can_confer` because pod role assignment reaches
    the rule without a request ``Context``: it is handed a user id by three
    services, and resolves that user's pod roles itself. Both entry points must
    refuse identically, which is why the comparison and the message are here and
    not duplicated at each site.
    """
    missing = permissions_not_held(held, requested)
    if not missing:
        return
    raise DomainError(
        f"You may not {action}: {', '.join(missing)}. "
        "Ask someone who holds them to make this change.",
        code=CONFERRAL_EXCEEDS_HOLDER,
        status_code=403,
        details={"permission_ids": missing},
    )
