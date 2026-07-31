import re


# Split a slug on underscores, hyphens, and dots.
_SEPARATORS = re.compile(r"[_\-.]+")


def humanize_name(value: str | None) -> str:
    """Render a machine-style name as spaced Title Case for human display.

    ``"abc_def"`` -> ``"Abc Def"``, ``"my-cool-pod"`` -> ``"My Cool Pod"``.

    Human-entered display names without machine separators are returned
    unchanged, so intentional capitalization like ``"Acme Support AI"`` is
    preserved. When a value contains machine separators, they are normalized
    even if another layer has already added spaces or capitalization. Tokens
    that are already mixed case (``iOS``, ``OpenAI``) are left intact.
    """
    if not value:
        return value or ""

    stripped = value.strip()
    if not stripped:
        return stripped

    has_machine_separator = _SEPARATORS.search(stripped) is not None
    if not has_machine_separator and any(character.isspace() for character in stripped):
        return stripped

    normalized = _SEPARATORS.sub(" ", stripped) if has_machine_separator else stripped
    tokens = normalized.split()
    if not tokens:
        return stripped

    humanized: list[str] = []
    for token in tokens:
        if token != token.lower() and token != token.upper():
            # Already mixed case — preserve as-is.
            humanized.append(token)
        else:
            humanized.append(token[:1].upper() + token[1:].lower())
    return " ".join(humanized)
