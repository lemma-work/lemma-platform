"""Positions in a pod's change log, and the opaque cursor that names one.

A position is ``(xid, seq)``: the writing transaction's id and the row's
sequence number. Read in that order below the snapshot's ``xmin`` it is final
and gap-free (see ``reader``). Callers outside this package hold cursors as
opaque strings; only this module knows how one is spelled, so the spelling can
change without a client noticing.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

_PREFIX = "c1."


class InvalidCursor(ValueError):
    """The string is not a change-log cursor (an old stream id, a typo, junk)."""


@dataclass(frozen=True, slots=True, order=True)
class ChangePosition:
    """Where a change sits in its pod's log; totally ordered."""

    xid: int
    seq: int

    @classmethod
    def start(cls) -> ChangePosition:
        """Before every change the log has ever held."""
        return cls(0, 0)

    @property
    def key(self) -> str:
        """``<xid>:<seq>``, the form domain rows record (``chg:<xid>:<seq>``)."""
        return f"{self.xid}:{self.seq}"

    @classmethod
    def from_key(cls, key: str) -> ChangePosition:
        xid, sep, seq = key.partition(":")
        if not sep:
            raise InvalidCursor(key)
        try:
            return cls(int(xid), int(seq))
        except ValueError as exc:
            raise InvalidCursor(key) from exc

    def __str__(self) -> str:
        return self.key


def encode_cursor(position: ChangePosition) -> str:
    raw = f"{position.xid}.{position.seq}".encode()
    return _PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> ChangePosition:
    """The position ``cursor`` names; ``InvalidCursor`` if it names none."""
    if not cursor.startswith(_PREFIX):
        raise InvalidCursor(cursor)
    body = cursor.removeprefix(_PREFIX)
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        xid, seq = raw.split(".")
        position = ChangePosition(int(xid), int(seq))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursor(cursor) from exc
    if position.xid < 0 or position.seq < 0:
        raise InvalidCursor(cursor)
    return position


__all__ = ["ChangePosition", "InvalidCursor", "decode_cursor", "encode_cursor"]
