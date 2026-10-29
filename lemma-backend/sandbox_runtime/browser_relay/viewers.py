"""Who is looking at this sandbox's display right now, and in which mode.

One registry for both transports. A VNC socket and a WebRTC signalling socket
are each one viewer for as long as they are open; the relay is per-sandbox, so
this is the only place "is anybody still watching" and "who holds the screen
size" have a single answer -- two API workers can each carry one viewer of the
same sandbox and neither of them can see the other's.

A viewer is named by the caller (`viewer=` on the socket), so the resize lease
can follow a person from their socket to the separate HTTP call that resizes.
Unnamed sockets -- an older frontend -- get a name of their own and simply
never hold the lease.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import re

from .stream_proxy import CONTROL, VIEW

#: What a caller may call its viewer. It travels in a query string and ends up
#: as a dictionary key and in log lines, so it is held to a shape that can do
#: neither harm.
_VIEWER_NAME = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")

_anonymous = itertools.count(1)


def viewer_name(raw: str | None) -> str:
    """The caller's name for this viewer if it is usable, else a fresh one."""
    if raw and _VIEWER_NAME.fullmatch(raw):
        return raw
    return f"anonymous-{next(_anonymous)}"


@dataclass
class _Viewer:
    mode: str
    transport: str
    #: A name can be open on two sockets at once for a moment: a reconnect
    #: opens the new socket before the old one's close is noticed.
    sockets: int = 1


@dataclass
class Viewers:
    """The live sockets, by viewer name."""

    _live: dict[str, _Viewer] = field(default_factory=dict)

    def join(self, name: str, *, mode: str, transport: str) -> None:
        current = self._live.get(name)
        if current is None:
            self._live[name] = _Viewer(mode=mode, transport=transport)
            return
        current.sockets += 1
        current.mode = mode
        current.transport = transport

    def leave(self, name: str) -> None:
        current = self._live.get(name)
        if current is None:
            return
        current.sockets -= 1
        if current.sockets <= 0:
            del self._live[name]

    def set_mode(self, name: str, mode: str) -> None:
        """A WebRTC viewer took or gave back control on an open socket.

        The streamer has one controller at a time and a newer one displaces
        the old, so taking control here demotes every other WebRTC viewer to
        match what the streamer just did. VNC viewers keep their mode: RFB is
        shared, and a VNC socket's mode is fixed when it opens.
        """
        current = self._live.get(name)
        if current is None:
            return
        if mode == CONTROL:
            for other_name, other in self._live.items():
                if other_name != name and other.transport == "rtc":
                    other.mode = VIEW
        current.mode = mode

    def mode_of(self, name: str) -> str | None:
        current = self._live.get(name)
        return current.mode if current else None

    def count(self) -> int:
        return sum(viewer.sockets for viewer in self._live.values())

    def summary(self) -> list[dict[str, str]]:
        """Names and modes, for `/health`. No addresses, nothing else."""
        return [
            {"viewer": name, "mode": viewer.mode, "transport": viewer.transport}
            for name, viewer in sorted(self._live.items())
        ]


#: The one registry this process has.
VIEWERS = Viewers()

__all__ = ["CONTROL", "VIEW", "VIEWERS", "Viewers", "viewer_name"]
