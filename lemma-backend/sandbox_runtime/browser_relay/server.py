"""Entrypoint: `python -m sandbox_runtime.browser_relay.server`.

Bound to 0.0.0.0 because the backend reaches it from outside the sandbox, and
what stops anyone else is the token plus the fabric's own door -- a Docker
private network, an E2B traffic token, a loopback port inside the desktop VM.
Binding loopback instead would make the relay unreachable on every fabric at
once, which is the state this replaced.
"""

from __future__ import annotations

import logging
import os

import uvicorn

from .app import DEFAULT_PORT, create_app


def main() -> None:
    # Every `agent-browser` this process runs -- the keepalive, the toolbar --
    # is a person's or the relay's, never the agent's. The wrapper reads this
    # to leave the "agent is driving" marker alone.
    os.environ["LEMMA_BROWSER_ACTOR"] = "relay"
    logging.basicConfig(
        level=os.environ.get("LEMMA_RELAY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(),
        host="0.0.0.0",  # noqa: S104 - see the module docstring
        port=DEFAULT_PORT,
        log_level="warning",
        access_log=False,
        # Off. Every frame `/vnc` carries is already JPEG or zlib, and
        # deflating it again for the API -- which inflated it and deflated it
        # once more for the browser -- was measured as the relay's largest
        # single cost: about half a core and tens of milliseconds a frame,
        # for bytes it could not shrink.
        ws_per_message_deflate=False,
    )


if __name__ == "__main__":
    main()
