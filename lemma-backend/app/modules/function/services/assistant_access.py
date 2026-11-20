"""Functions a Lemma keeps out of its assistant's reach (``assistant_invoke``).

A plugin's app may call a function only after checking something the app
owns -- a daily limit, a confirmation. The assistant, offered every function
and able to run any by name, stepped around that. A function with
``assistant_invoke`` false is neither listed to the assistant nor run for it.

Only the assistant choosing to run it is refused: an app signed in with the
assistant's standing is the app (``core/authorization/app_sessions``), and a
workflow, a schedule, another function or a named agent granted the function
each run it as themselves. ``contacts_invoke`` is its own opt-in, for a
contact's conversation, and is not narrowed by this.
"""

from __future__ import annotations

from app.core.authorization.app_sessions import is_the_assistant
from app.core.authorization.context import Context
from app.modules.function.domain.entities import FunctionEntity
from app.modules.function.domain.errors import FunctionNotForAssistantError


def offered_to_the_assistant(function: FunctionEntity) -> bool:
    return function.assistant_invoke


def require_runnable_by(function: FunctionEntity, ctx: Context) -> None:
    """Refuse the assistant a function kept from it; anyone else passes."""
    if not offered_to_the_assistant(function) and is_the_assistant(ctx):
        raise FunctionNotForAssistantError(function.name)


__all__ = ["offered_to_the_assistant", "require_runnable_by"]
