"""What a person's first organization and pod are called when nobody typed a name.

Ported from `lemma-frontend/components/onboarding/account-onboarding-helpers.ts`,
which did all of this in TypeScript because signing up through the app was the
only way in. Chat surfaces onboard people who never load the app at all, and a
second copy of "what is this workspace called" would drift from the first inside
one release -- so this is the implementation, and the web onboarding is being
moved onto it rather than kept beside it.

The generated names are deterministic on the address, not random. Someone whose
first attempt half-failed and who tries again lands on the same name, instead of
collecting a second differently-named workspace; and a name in a support ticket
can be reproduced from the email that made it.

The hash is FNV-1a as JavaScript computes it -- 32-bit wrapping, `Math.imul`
semantics, an unsigned shift for the noun and a signed value for the adjective.
Kept bit-exact rather than merely similar, because the two implementations have
to agree on the name for the same person for as long as both exist.
"""

from __future__ import annotations

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


def organization_name_candidate(
    *, email: str, work_domain: str | None = None, attempt: int = 0
) -> str:
    """What to call the organization on this attempt.

    Later attempts exist because the name may already be taken: the company
    name, then the bare domain, then numbered, and finally a generated name that
    no company will collide with.
    """
    company_name = (
        organization_name_from_work_domain(work_domain) if work_domain else None
    )
    if not company_name:
        return generated_organization_name(email, attempt)

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
