"""Choosing the address people will use to email an agent.

Readable on purpose: this is shown in the UI as "email this agent at …" and
typed by humans, so ``ops-assistant.acme@…`` beats ``pod-9f3c1e…@…`` even though
both are unique. That readability is exactly what makes collisions possible,
which is why the local part is derived deterministically and a random suffix is
added only when the deterministic form is already taken.

The pod assistant's address is the pod slug alone, with no agent half — it is
the pod answering, not one agent among several. That makes it the one local part
minted here that is a bare word, and a bare word on a catch-all domain shared by
every organization is claimable as ``postmaster@`` or ``abuse@`` by whoever names
a pod that. Stopping it is what :data:`RESERVED_LOCAL_PARTS` is for.

Pure functions. Uniqueness is ultimately enforced by a unique index on
``agent_surfaces.surface_identity_email`` — the database is the arbiter, and
these helpers exist to make a clash rare and to produce the next candidate when
one happens.
"""

from __future__ import annotations

import re
import secrets

# The one name that must never reach an address. Imported rather than
# spelled again so it cannot drift from the row it describes.
from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME

# RFC 5321 caps the local part at 64 octets. Longer and the address is silently
# truncated or rejected somewhere downstream, and the reply never comes back.
MAX_LOCAL_PART = 64

# Enough entropy that a second clash is not worth planning for, short enough to
# keep the address sayable out loud.
_SUFFIX_LENGTH = 4
_SUFFIX_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no look-alikes

# Local parts nobody may be allocated, whatever they named their pod.
#
# The backbone is RFC 2142, which reserves these on any domain that receives
# mail: a person is expected behind ``postmaster@`` and ``abuse@``, and handing
# either to the first tenant who named a pod that would route bounce and abuse
# traffic into a stranger's agent conversation. The rest are addresses a
# platform sends *from*, or that a reader takes to mean "this is Lemma talking,
# not one of its tenants".
#
# Reachable, not theoretical. The pod assistant takes the pod slug alone, so a
# pod named "Support" asks for `support@` on a domain every organization shares.
# `candidate_addresses` drops the request and falls through to a suffixed form —
# the same thing it already does when another pod holds the name.
#
# Written without separators, and compared against a candidate with its own
# separators stripped — see :func:`is_reserved`. Pod names admit spaces,
# hyphens and underscores, all of which `slugify` collapses to a hyphen, so
# screening the literal spelling alone caught `postmaster@` and let `Post
# Master` through as `post-master@`. One entry now covers every way of writing
# it.
RESERVED_LOCAL_PARTS = frozenset(
    {
        # RFC 2142 §3-5: operations, protocol support, business roles.
        "abuse",
        "noc",
        "security",
        "postmaster",
        "hostmaster",
        "webmaster",
        "usenet",
        "news",
        "www",
        "uucp",
        "ftp",
        "info",
        "marketing",
        "sales",
        "support",
        # Conventional senders and system mailboxes.
        "admin",
        "administrator",
        "root",
        "help",
        "billing",
        "contact",
        "hello",
        "noreply",
        "donotreply",
        "bounce",
        "bounces",
        "mailerdaemon",
        # Lemma's own identity on Lemma's own domain. Enumerated compounds
        # rather than a `lemma*` prefix rule, because the suffixed form of a pod
        # honestly named "Lemma" is `lemma-7rmt@` — a prefix rule would reject
        # that too, and go on rejecting every candidate until the bounded loop
        # in `candidate_addresses` gave up and returned nothing, leaving the pod
        # with no mailbox at all. These entries cannot do that: `lemma-support`
        # normalizes to `lemmasupport` and is refused, while `lemma-support-x9k2`
        # normalizes to `lemmasupportx9k2` and is not.
        "lem",
        "lemma",
        "lemmaadmin",
        "lemmabilling",
        "lemmahelp",
        "lemmanotifications",
        "lemmasecurity",
        "lemmasupport",
        "lemmateam",
    }
)


def slugify(value: str | None, *, fallback: str = "agent") -> str:
    """Lower-case, hyphenated, safe for the local part of an address."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or fallback


def random_suffix() -> str:
    return "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(_SUFFIX_LENGTH))


def is_reserved(local_part: str) -> bool:
    """Whether this local part is one nobody may be allocated.

    Matches the whole local part, never a prefix: ``triage.support@`` is an
    agent in a pod named "Support" and is fine, while ``support@`` is the shared
    domain's business address and is not.

    Hyphens and underscores are stripped before the comparison, so one entry
    covers every spelling a pod name can reach. Pod names allow spaces, hyphens
    and underscores and `slugify` turns all three into a hyphen, which is how
    ``postmaster@`` was refused while "Post Master" quietly took
    ``post-master@`` — the same address to anyone reading it.

    The dot is deliberately *not* stripped. It is the agent/pod separator, so a
    local part containing one is an agent's address rather than a bare role
    word, and collapsing it would refuse an agent named "Sale" in a pod named
    "S" for looking like ``sales@`` when nobody reading ``sale.s@`` would think
    so.
    """
    return _normalize_for_reservation(local_part) in RESERVED_LOCAL_PARTS


def _normalize_for_reservation(local_part: str) -> str:
    """Casefold and drop the separators that only vary the spelling."""
    return re.sub(r"[-_]+", "", local_part.strip().lower())


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

    Bare is also why :func:`is_reserved` exists: ``acme@`` is fine and
    ``postmaster@`` is not, and only the assistant's form can produce either.

    The assistant's *stored* name is treated the same as no name. It has a row
    now, so callers that used to hold ``None`` for it hold ``"pod_default"``
    instead, and that is an internal identifier rather than something to ask a
    person to type -- letting it through would mint ``pod-default.acme@`` for a
    pod that already answers at ``acme@``. Refused here as well as at the call
    sites, because the cost of missing one is a second address that then has to
    keep working forever.
    """
    pod = slugify(pod_name, fallback="pod")
    tail = f"-{suffix}" if suffix else ""
    if agent_name is None or agent_name == DEFAULT_POD_AGENT_NAME:
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
    """The plain address first, then suffixed alternatives to try in order.

    A reserved form is dropped rather than offered and refused, and the list
    grows to keep the budget at ``attempts``. Spending one of five attempts on an
    address that can never be allocated would make a reserved name likelier to
    end with no mailbox at all — the one outcome worse than an ugly address.

    Every candidate is screened, not only the plain one. A suffixed form is
    vanishingly unlikely to be reserved, but working out *why* means reasoning
    about whether any entry ends in a four-character hyphenated segment, and
    that reasoning silently expires the day somebody adds one. The generation
    loop is bounded for the same reason: a filter that can reject must not be
    able to spin.

    Screening every candidate is also why :data:`RESERVED_LOCAL_PARTS` may not
    hold prefix rules. A rule matching `lemma*` would reject not just
    ``lemma-support@`` but every suffixed candidate for a pod honestly named
    "Lemma", and this loop would exhaust its bound and return an empty list —
    trading an ugly address for no mailbox at all.
    """
    wanted = max(1, attempts)
    candidates: list[str] = []
    plain = build_agent_email(agent_name=agent_name, pod_name=pod_name, domain=domain)
    if not is_reserved(_local_part_of(plain)):
        candidates.append(plain)

    for _ in range(wanted * 4):
        if len(candidates) >= wanted:
            break
        address = build_agent_email(
            agent_name=agent_name,
            pod_name=pod_name,
            domain=domain,
            suffix=random_suffix(),
        )
        if not is_reserved(_local_part_of(address)):
            candidates.append(address)
    return candidates


def _local_part_of(address: str) -> str:
    return address.split("@", 1)[0]


__all__ = [
    "MAX_LOCAL_PART",
    "RESERVED_LOCAL_PARTS",
    "build_agent_email",
    "build_local_part",
    "candidate_addresses",
    "is_reserved",
    "random_suffix",
    "slugify",
]
