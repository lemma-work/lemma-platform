"""What the API server reads off the wire before the app sees it.

uvicorn assembles a websocket message whole before handing it to the app, so a
check in the app -- the live socket's 16 KiB frame limit -- comes after the
memory is spent. ``WS_MAX_BYTES`` is the server's own ceiling: a message past it
is refused while it arrives, and the socket closed with 1009 (message too big).

It is one value for every socket the API serves, and every launcher passes it:
``ws_max_size=WS_MAX_BYTES`` where uvicorn is started from Python,
``--ws-max-size 1048576`` where it is started from a command line (the
Dockerfile, Railway, compose, ``make dev``, Desktop and the local stack). The
image also sets ``UVICORN_WS_MAX_SIZE`` for a command that leaves the flag out.
``test_ws_max_size_everywhere`` holds every launcher to this number.

Why 1 MiB: nothing a client means to send is larger. Files and other bulk
bytes travel over the HTTP API, never a socket -- a socket carries what
changed, not what was stored. That includes the Agent Host link
(``/agent-host/link``): file chunks and run-event images cross
``/agent-host/transfers/{id}`` and ``/agent-host/runs/{run_id}/blobs/{sha256}``
(``agent.domain.agent_host_transfer``), and the host bounds what remains on
the socket. A host that sends a larger message is closed with 1009.
"""

from __future__ import annotations

#: The largest websocket message the API server reads: 1 MiB.
WS_MAX_BYTES = 1024 * 1024

#: Whether the API negotiates permessage-deflate. Off, and passed by every
#: launcher beside the cap (``--ws-per-message-deflate false``), because
#: uvicorn decides it for the whole server and the socket that dominates the
#: bytes is the browser view: VNC frames that are already JPEG or zlib, which
#: the API used to inflate from the relay and deflate again for the browser.
#: The stream bench measured that recompression at tens of milliseconds a frame
#: for no saving. The JSON sockets give up a compression they barely needed --
#: a socket carries what changed, which is small.
WS_PER_MESSAGE_DEFLATE = False

__all__ = ["WS_MAX_BYTES", "WS_PER_MESSAGE_DEFLATE"]
