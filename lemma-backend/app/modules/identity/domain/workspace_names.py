"""What a person's first organization and pod are called when nobody typed a name.

Ported from the web app's onboarding helpers, which did all of this in
TypeScript because signing up through the app was the only way in. Chat
surfaces onboard people who never load the app at all, and a second copy of
"what is this workspace called" would drift from the first inside one release
-- so this is the implementation, and the web onboarding calls it rather than
keeping its own.

A company's organization is named after the company its work domain names. A
workspace of one is named after its person -- "Ada's workspace" -- and never
after a generated phrase: a name that says whose it is beats one that is merely
unique, and the handle, not the name, is what has to be unique.

The generated adjective-noun names survive only as the last rung of the company
ladder. They are deterministic on the address, not random, so a retry lands on
the same name. The hash is FNV-1a as JavaScript computes it -- 32-bit wrapping,
`Math.imul` semantics, an unsigned shift for the noun and a signed value for the
adjective -- kept bit-exact so the expected values in the unit tests stay the
ones the TypeScript produced.
"""

from __future__ import annotations

import re
import unicodedata

_ADJECTIVES = (
    "Amber",
    "Bright",
    "Cedar",
    "Clear",
    "Copper",
    "Golden",
    "Indigo",
    "North",
    "Olive",
    "Open",
    "Quiet",
    "Silver",
    "Wild",
)

_NOUNS = (
    "Atlas",
    "Bridge",
    "Compass",
    "Field",
    "Forge",
    "Garden",
    "Grove",
    "Harbor",
    "Lantern",
    "Meadow",
    "Orchard",
    "Signal",
    "Studio",
    "Summit",
)

# Country-code domains whose second label is a registry, not the company:
# `acme.co.uk` is Acme, not Co.
_REGISTRY_SECOND_LEVEL_LABELS = frozenset(
    {"ac", "co", "com", "edu", "gov", "net", "org"}
)

_SMALL_WORDS = frozenset(
    {"a", "an", "and", "for", "from", "in", "of", "the", "to", "with"}
)

# How many company-shaped names to offer before falling back to a generated one.
_COMPANY_NAME_ATTEMPTS = 10

_UINT32 = 0xFFFFFFFF
_INT32_SIGN_BIT = 0x80000000

_FNV_OFFSET_BASIS = 2166136261
_FNV_PRIME = 16777619


def to_title_case(value: str) -> str:
    """Title case that leaves joining words alone after the first."""
    words = [word for word in value.split(" ") if word]
    titled = []
    for index, word in enumerate(words):
        lower = word.lower()
        if index > 0 and lower in _SMALL_WORDS:
            titled.append(lower)
        else:
            titled.append(lower[:1].upper() + lower[1:])
    return " ".join(titled)


def _js_char_code(character: str) -> int:
    """`String.prototype.charCodeAt(0)` for one code point.

    JavaScript iterates a string by code point but reads a UTF-16 unit, so a
    character outside the basic plane contributes its high surrogate. Reached
    only by an address with an emoji in it, and reproduced anyway so the two
    implementations cannot disagree about one.
    """
    code = ord(character)
    if code <= 0xFFFF:
        return code
    return 0xD800 + ((code - 0x10000) >> 10)


def generated_organization_name(seed: str, attempt: int = 0) -> str:
    """An adjective and a noun, chosen by hashing the address.

    The fallback for a personal address, where there is no company to name the
    organization after.
    """
    normalized = seed.strip().lower() or "lemma"

    unsigned = _FNV_OFFSET_BASIS
    for character in normalized:
        unsigned = (unsigned ^ _js_char_code(character)) & _UINT32
        unsigned = (unsigned * _FNV_PRIME) & _UINT32

    # `Math.abs(hash + ...)` reads the signed value; `hash >>> 8` the unsigned.
    signed = unsigned - 0x100000000 if unsigned & _INT32_SIGN_BIT else unsigned

    adjective = _ADJECTIVES[abs(signed + attempt * 17) % len(_ADJECTIVES)]
    noun = _NOUNS[abs((unsigned >> 8) + attempt * 29) % len(_NOUNS)]
    return f"{adjective} {noun}"


def organization_name_from_work_domain(domain: str) -> str | None:
    """The company a work domain names, or ``None`` when it names none."""
    labels = [label for label in domain.strip().lower().lstrip("@").split(".") if label]
    if len(labels) < 2:
        return None

    top_level = labels[-1]
    second_level = labels[-2]
    names_registry = (
        len(labels) >= 3
        and len(top_level) == 2
        and second_level in _REGISTRY_SECOND_LEVEL_LABELS
    )
    label = labels[-3] if names_registry else second_level
    return to_title_case(label.replace("-", " ").replace("_", " ")) or None


#: Where a local part splits into words: "ada.lovelace", "ada_l", "ada-l".
_LOCAL_PART_SEPARATORS = re.compile(r"[._-]")

#: What a workspace of one is called when nothing says whose it is.
UNNAMED_PERSONAL_ORGANIZATION = "My workspace"

#: The Lemma made with a workspace of one.
PERSONAL_LEMMA_NAME = "Personal"

#: The Lemma made with a team's organization, when its name folds to nothing a
#: pod may be called -- an organization named entirely in a non-Latin script.
UNNAMED_TEAM_LEMMA = "Everyone"


def first_name_from_address(email: str | None) -> str | None:
    """A first name an address spells out, or ``None`` when it spells none.

    A mailbox is not a name -- the web once offered somebody "ada0196+99's
    workspace" -- so a local part counts only when its first word reads as one:
    letters and nothing else, at least two of them, before any ``+tag``, dot,
    underscore or hyphen.
    """
    said = (email or "").strip()
    at = said.rfind("@")
    if at < 1:
        return None
    first = _LOCAL_PART_SEPARATORS.split(said[:at].split("+")[0])[0]
    if len(first) < 2 or not first.isalpha():
        return None
    return first[:1].upper() + first[1:].lower()


def personal_organization_name(full_name: str | None, email: str | None = None) -> str:
    """ "Ada's workspace": the name they gave, else one their address spells.

    A personal workspace is named after its person, never numbered: the web
    onboarding once produced "Personal 5", which says nothing about whose it is.
    """
    words = (full_name or "").split()
    first = words[0] if words else first_name_from_address(email)
    return f"{first}'s workspace" if first else UNNAMED_PERSONAL_ORGANIZATION


def organization_lemma_name(organization_name: str, *, team: bool) -> str:
    """What the Lemma made with a new organization is called.

    A team's is called what the team is, folded to the characters a pod name
    allows; a workspace of one has one Lemma, and it is that person's.
    """
    if not team:
        return PERSONAL_LEMMA_NAME
    # A pod name starts and ends with a letter or digit.
    return _pod_name_safe(organization_name).strip(" _-") or UNNAMED_TEAM_LEMMA


def organization_name_candidate(
    *,
    email: str,
    work_domain: str | None = None,
    attempt: int = 0,
    full_name: str | None = None,
) -> str:
    """What to call the organization on this attempt.

    Later attempts exist because the name may already be taken: the company
    name, then the bare domain, then numbered, and finally a generated name that
    no company will collide with. Without a company it is the person's own
    workspace, named after them on every attempt -- a name is a label two
    organizations may share, so there is nothing to walk away from.
    """
    company_name = (
        organization_name_from_work_domain(work_domain) if work_domain else None
    )
    if not company_name:
        return personal_organization_name(full_name, email)

    if attempt == 0:
        return company_name
    if attempt == 1:
        return work_domain or company_name
    if attempt < _COMPANY_NAME_ATTEMPTS:
        return f"{company_name} {attempt}"
    return generated_organization_name(email, attempt - _COMPANY_NAME_ATTEMPTS)


def first_pod_name(full_name: str | None) -> str:
    """ "Ada Pod", or "Personal Pod" when we have no name to use.

    A pod name is addressable from the CLI and the server accepts only letters,
    digits, spaces, hyphens and underscores, so a real name has to be folded to
    that set here rather than discovered as a rejected create -- apostrophes in
    "O'Brien", accents in "José".
    """
    first = (full_name or "").strip().split(" ")[0] if full_name else ""
    safe = _pod_name_safe(first)
    return f"{safe} Pod" if safe else "Personal Pod"


def _pod_name_safe(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
        and (character.isascii() and (character.isalnum() or character in " _-"))
    )
    return " ".join(stripped.split())
