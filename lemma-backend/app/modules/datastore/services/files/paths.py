"""Canonical datastore path/name normalization.

Single source of truth shared by the file path policy and the system-skills
overlay. Paths are always absolute (leading ``/``), have no empty/relative
segments, and never end in a trailing slash (except the root ``/``).

Every write and every read funnels through here -- `create_file`, folder create,
move, and each by-path lookup -- so a rule stated once applies to a web upload, a
CLI upload, an attachment off any surface, and the file the agent asks for by
name, without any of them knowing about it.
"""

from __future__ import annotations

import unicodedata

from app.modules.datastore.domain.errors import DatastoreValidationError


def fold_confusable_characters(name: str) -> str:
    """Reduce a name to characters somebody can actually retype.

    A file is found by matching its path exactly, and a person does not supply
    the path -- they read it and repeat it, or a model does. So a character that
    *renders* as a space but is not one makes the file permanently unreachable:
    the row says one thing, every request says another, and nothing about the
    error tells you they differ. There is nothing to escape and nothing to see.

    macOS is what makes this ordinary rather than exotic. Every screenshot since
    Ventura is named ``Screenshot ... at 6.37.26<U+202F>PM.png`` -- a NARROW
    NO-BREAK SPACE before the meridiem -- and dragging one into a conversation is
    the single most common way anybody hands an agent an image. One such file on
    dev cost an agent twelve tool calls and it never did read the screenshot.

    Two folds, both narrow:

    * **Space separators become an ordinary space.** Every category ``Zs``
      character: U+00A0, U+202F, U+2007, the U+2000-200A run, U+205F, U+3000.
    * **Format characters are dropped.** Category ``Cf`` -- zero-width space,
      joiners, the BOM, the bidi overrides. These are invisible, so a name
      carrying one is indistinguishable on screen from a name that does not.

    NFC first, so ``café`` written decomposed and ``café`` written composed are
    one name rather than two rows that look identical in every listing.

    Deliberately not a slug. Spaces, case, punctuation and non-Latin scripts all
    survive: the name is the person's, and the goal is only that what they see is
    what they can ask for.
    """
    folded: list[str] = []
    for character in unicodedata.normalize("NFC", name):
        category = unicodedata.category(character)
        if category == "Zs":
            folded.append(" ")
        elif category == "Cf":
            continue
        else:
            folded.append(character)
    return "".join(folded)


def normalize_datastore_name(name: str) -> str:
    """Validate a single path segment (a file or folder name)."""
    cleaned = fold_confusable_characters(name).strip()
    if not cleaned:
        raise DatastoreValidationError("File name cannot be empty")
    if "/" in cleaned:
        raise DatastoreValidationError("Names cannot contain '/'")
    if cleaned in {".", ".."}:
        raise DatastoreValidationError("Invalid path segment")
    return cleaned


def normalize_datastore_path(path: str | None) -> str:
    """Normalize an absolute datastore path; reject relative segments."""
    if path is None:
        return "/"
    raw = path.strip()
    if not raw:
        return "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    parts = [segment for segment in raw.split("/") if segment]
    normalized_parts: list[str] = []
    for part in parts:
        if part in {".", ".."}:
            raise DatastoreValidationError("Relative path segments are not allowed")
        normalized_parts.append(normalize_datastore_name(part))
    if not normalized_parts:
        return "/"
    return "/" + "/".join(normalized_parts)
