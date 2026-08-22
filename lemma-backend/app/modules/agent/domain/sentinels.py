"""The "caller did not mention this field" sentinel, for PATCH semantics.

Three copies of this existed — one in `runtime_profiles` with this docstring,
one in `conversation_service` with none, and a bare `object()` in
`agent_service`. The bare one is the reason this is now shared: `object()` has
no `__repr__`, so a stray sentinel reaching a log or an error message printed as
`<object object at 0x…>`, and it type-hints as `object`, which makes
`str | None | object` mean nothing at all to a checker.
"""

from __future__ import annotations


class UnsetType:
    """Distinguishes "the caller did not mention this field" from "null".

    A PATCH that omits ``api_key`` must keep the stored one; a PATCH that sends
    ``null`` must clear it. Both arrive as absent-or-None without a sentinel,
    and defaulting either way silently destroys or ignores a credential.
    """

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNSET"


UNSET = UnsetType()
