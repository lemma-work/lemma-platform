"""Who may open a function to contacts, and which functions may be opened.

Opening a function lets people outside the pod start it, as the function
itself, on every turn of their conversation. That is a decision about the pod,
so it takes the pod-settings permission (``pod.update``). Editors hold that
permission too, so it also takes either owning the function or administering
the pod (``pod.member.manage``, which only admins and organization owners
hold). An editor cannot open a colleague's function to strangers.
"""

from __future__ import annotations

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.function.domain.entities import FunctionEntity
from app.modules.function.domain.errors import FunctionDomainError

#: The input key the platform sets to the asking contact.
_CONTACT_INPUT_KEY = "contact_id"


async def require_contacts_opener(ctx: Context, function: FunctionEntity) -> None:
    """Refuse anybody but the function's owner or a pod admin, with pod.update."""
    pod = ResourceRef.pod(function.pod_id)
    await ctx.require(Permissions.POD_UPDATE, pod)
    if ctx.user_id is not None and ctx.user_id == function.user_id:
        return
    if await ctx.can(Permissions.POD_MEMBER_MANAGE, pod):
        return
    raise FunctionDomainError(
        "Only the function's owner or a pod admin can open it to contacts.",
        code="FUNCTION_CONTACTS_OWNER_OR_ADMIN",
        status_code=403,
    )


def require_contact_input(function: FunctionEntity) -> None:
    """Refuse a function whose input does not declare ``contact_id``.

    The platform overwrites that key with the asking contact. A function that
    never declared it would either reject every call or ignore who is asking,
    and acting for a contact without knowing which one is how one contact's
    request reaches another's rows.
    """
    properties = (function.input_schema or {}).get("properties")
    if isinstance(properties, dict) and _CONTACT_INPUT_KEY in properties:
        return
    raise FunctionDomainError(
        "A function opened to contacts must declare a contact_id input: the "
        "platform fills it with the contact who asked.",
        code="FUNCTION_CONTACT_INPUT_REQUIRED",
        status_code=422,
    )
