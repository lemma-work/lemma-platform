"""Choosing the address people will use to email an agent.

Readable on purpose: this is shown in the UI as "email this agent at …" and
typed by humans, so ``ops-assistant.acme@…`` beats ``pod-9f3c1e…@…`` even though
both are unique. That readability is exactly what makes collisions possible,
which is why the local part is derived deterministically and a random suffix is
added only when the deterministic form is already taken.

Pure functions. Uniqueness is ultimately enforced by a unique index on
``agent_surfaces.surface_identity_email`` — the database is the arbiter, and
these helpers exist to make a clash rare and to produce the next candidate when
one happens.
"""

from __future__ import annotations

import re
import secrets

# RFC 5321 caps the local part at 64 octets. Longer and the address is silently
# truncated or rejected somewhere downstream, and the reply never comes back.
MAX_LOCAL_PART = 64

# Enough entropy that a second clash is not worth planning for, short enough to
# keep the address sayable out loud.
_SUFFIX_LENGTH = 4
_SUFFIX_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no look-alikes


def slugify(value: str | None, *, fallback: str = "agent") -> str:
    """Lower-case, hyphenated, safe for the local part of an address."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or fallback


def random_suffix() -> str:
    return "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(_SUFFIX_LENGTH))


def build_local_part(
    *, agent_name: str | None, pod_name: str | None, suffix: str | None = None
) -> str:
    """``{agent}.{pod}`` — plus a suffix when the plain form is taken.

    The agent slug is the identifying half, so when the budget is tight the pod
    slug is truncated first and the agent name is what survives. A person
    reading ``support-triage.a…@`` can still tell which agent it is.

    ``agent_name=None`` is the pod assistant, and it gets the pod slug alone:
    ``acme@`` rather than ``acme.acme@``. The assistant is not one agent among
    several, it is the pod answering, so the pod's own name is the honest
    address — and it is the shortest thing a person can be asked to type.
    """
    pod = slugify(pod_name, fallback="pod")
    tail = f"-{suffix}" if suffix else ""
    if agent_name is None:
        return f"{pod[: MAX_LOCAL_PART - len(tail)]}{tail}".strip("-.")

    agent = slugify(agent_name)

    # Everything except the pod slug is fixed; give the pod whatever is left.
    room_for_pod = MAX_LOCAL_PART - len(agent) - len(tail) - 1  # 1 for the dot
    if room_for_pod < 1:
        # A pathologically long agent name: keep the address valid by cutting
        # the agent slug itself, and drop the pod half entirely.
        return f"{agent[: MAX_LOCAL_PART - len(tail)]}{tail}".strip("-.")
    return f"{agent}.{pod[:room_for_pod]}{tail}".strip("-.")


def build_agent_email(
    *,
    agent_name: str | None,
    pod_name: str | None,
    domain: str,
    suffix: str | None = None,
) -> str:
    return f"{build_local_part(agent_name=agent_name, pod_name=pod_name, suffix=suffix)}@{domain.strip().lower()}"


def candidate_addresses(
    *, agent_name: str | None, pod_name: str | None, domain: str, attempts: int = 5
) -> list[str]:
    """The plain address first, then suffixed alternatives to try in order."""
    first = build_agent_email(agent_name=agent_name, pod_name=pod_name, domain=domain)
    return [first] + [
        build_agent_email(
            agent_name=agent_name,
            pod_name=pod_name,
            domain=domain,
            suffix=random_suffix(),
        )
        for _ in range(max(0, attempts - 1))
    ]


__all__ = [
    "MAX_LOCAL_PART",
    "build_agent_email",
    "build_local_part",
    "candidate_addresses",
    "random_suffix",
    "slugify",
]
