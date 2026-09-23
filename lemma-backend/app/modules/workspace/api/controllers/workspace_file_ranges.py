"""Conditional and partial reads of a workspace file.

Extracted from the controller, which was at the size limit. This is the part
with no dependency on anything else in it: a `Range` header and an
`If-None-Match` are pure text, and what they mean is decided entirely by the
file's length and digest.
"""

from __future__ import annotations

#: The most any single response will carry, range or not.
MAX_CONTENT_BYTES = 8 * 1024 * 1024


class Unsatisfiable:
    """A `Range` that names nothing this file has.

    A type of its own rather than a bare `object` sentinel so the caller's
    `isinstance` actually narrows -- with `object` in the union, unpacking the
    tuple case does not typecheck, and silencing that would be silencing the
    check that makes this safe to unpack at all.
    """


UNSATISFIABLE = Unsatisfiable()


def matches_etag(if_none_match: str, etag: str) -> bool:
    """Whether the caller already holds this exact content.

    `*` matches anything, and a list is comma-separated. Weak validators
    (`W/"..."`) compare equal to their strong form for this purpose: the
    question is only "is this the same bytes".
    """
    candidates = [part.strip() for part in if_none_match.split(",")]
    if "*" in candidates:
        return True
    return any(part.removeprefix("W/") == etag for part in candidates)


def requested_range(
    header: str | None, total: int
) -> tuple[int, int] | Unsatisfiable | None:
    """A `Range` header as an offset and a length, or `None` for the whole file.

    Only `bytes=` with a single range: multipart ranges would mean building a
    multipart body, and nothing that reads a workspace file asks for one. A
    header this does not understand is ignored rather than refused, which is
    what RFC 9110 asks for -- the caller gets the whole file, which is always
    a correct answer.

    A suffix range (`bytes=-500`, the last 500 bytes) is supported because it
    is how a reader peeks at the end of a log.
    """
    if not header or not header.lower().startswith("bytes="):
        return None
    spec = header[len("bytes=") :].strip()
    if "," in spec or "-" not in spec:
        return None
    first, _, last = spec.partition("-")
    try:
        if not first:
            length = int(last)
            if length <= 0:
                return None
            if total == 0:
                # There is no last byte of an empty file. Falling through
                # produced `(0, 0)`, which renders as `bytes 0--1/0` -- a
                # malformed header for a range that cannot be satisfied.
                return UNSATISFIABLE
            start = max(total - length, 0)
            # The same ceiling the ordinary branch applies. Without it
            # `bytes=-999999999` read far more in one response than
            # `bytes=0-999999999` would, which is the cap the whole-file
            # reader is built around.
            return start, min(length, total - start, MAX_CONTENT_BYTES)
        start = int(first)
        end = int(last) if last else total - 1
    except ValueError:
        return None
    if start >= total or start > end:
        return UNSATISFIABLE
    end = min(end, total - 1)
    return start, min(end - start + 1, MAX_CONTENT_BYTES)
