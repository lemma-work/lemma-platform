"""How a public link's allowance is counted.

A link is live — it serves the file at its path as it is now — so its budget is
counted in opens of that file, not in bytes fixed when it was minted. A page
that doubles in size after it was shared must not quietly halve the opens it has
left. What the page embeds is counted separately, in bytes, because a reader
looking at a page with eight pictures opened it once, not nine times.
"""

from __future__ import annotations

#: One open of a link, in the units its budget is kept in. Fine enough that a
#: one-byte range of a large file still costs something.
OPEN_UNITS = 1_000_000

#: What a shared page's embedded files may serve, in bytes, per open of its
#: budget. Bounds egress through a leaked page link the way the opens bound the
#: page itself.
EMBEDDED_BYTES_PER_OPEN = 25 * 1024 * 1024


def open_units(billable_bytes: int, size_bytes: int) -> int:
    """What a response costs, as a fraction of one open of the file as it is now.

    Never more than one open, so a file that shrank or grew since its size was
    recorded cannot charge a reader twice for one download; never zero for a
    response that moves bytes, so slicing a large file finely is not free.
    """
    if billable_bytes <= 0:
        return 0
    if size_bytes <= 0:
        return OPEN_UNITS
    return max(1, min(OPEN_UNITS, billable_bytes * OPEN_UNITS // size_bytes))
