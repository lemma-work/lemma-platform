"""Pod role names, and the normalisation core applies to them.

Core already owned the set -- `SYSTEM_POD_ROLES` was four string literals in
`service.py`, and `SYSTEM_ROLE_PERMISSIONS` in `permissions.py` is keyed by
them. What it did *not* own was the function that turns a name into one of
them, so `app/core/authorization/service.py` imported
`app.modules.pod.domain.visibility` for it: core defined the roles and asked a
module how to spell them.

The enum stays in `mod:pod`, which is where a pod role is a domain concept.
`PodRole` is a `str` mixin, so these work on one unchanged -- see the comment on
`normalize_role_name` for the one detail that makes that true.
"""

from __future__ import annotations

from collections.abc import Iterable

#: The four roles every pod is provisioned with.
SYSTEM_POD_ROLE_NAMES: frozenset[str] = frozenset(
    {"POD_VIEWER", "POD_USER", "POD_EDITOR", "POD_ADMIN"}
)

#: Short spellings accepted on the way in. A person writing `EDITOR` in a bundle
#: or an API call means `POD_EDITOR`; nothing renders these, so the mapping is
#: one-way.
ROLE_ALIASES: dict[str, str] = {
    "VIEWER": "POD_VIEWER",
    "USER": "POD_USER",
    "EDITOR": "POD_EDITOR",
    "ADMIN": "POD_ADMIN",
}

MAX_ROLE_NAME_LENGTH = 120


def normalize_role_name(value: str) -> str:
    """Canonical spelling of one role name.

    `value.strip()` rather than `str(value).strip()`, and the difference is not
    cosmetic: a `str`-mixin enum's `str()` is `"PodRole.ADMIN"` while its *str
    methods* operate on `"POD_ADMIN"`. Calling the method directly is what lets
    this take a `PodRole` without importing one -- and what the version in
    `mod:pod` needed an `isinstance` branch to work around.
    """
    normalized = value.strip().upper()
    normalized = ROLE_ALIASES.get(normalized, normalized)
    if not normalized:
        raise ValueError("Role name is required")
    if len(normalized) > MAX_ROLE_NAME_LENGTH:
        raise ValueError(
            f"Role name must be {MAX_ROLE_NAME_LENGTH} characters or fewer"
        )
    if not all(char.isalnum() or char in {"_", "-"} for char in normalized):
        raise ValueError(
            "Role names may contain only letters, numbers, underscore, and dash"
        )
    return normalized


def normalize_role_list(values: Iterable[str] | None) -> list[str]:
    """Normalise every name, dropping duplicates and keeping the given order."""
    seen: set[str] = set()
    roles: list[str] = []
    for value in values or []:
        role = normalize_role_name(value)
        if role in seen:
            continue
        seen.add(role)
        roles.append(role)
    return roles
