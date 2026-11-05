"""Bulk bytes between Lemma and an Agent Host travel over HTTP, not the link.

The API server reads no websocket message past ``WS_MAX_BYTES`` (1 MiB; see
``app/core/server_limits.py``), and a socket is the wrong place for a file in
any case: one large frame holds up every heartbeat and event queued behind it.
So the link carries only *what* moves -- an id, a size, a digest -- and the
bytes go over two host-authenticated HTTP routes beside it. See
docs/architecture/desktop-host-execution.md §4 and
docs/architecture/agent-host.md#bulk-bytes.

**Transfers** carry one ``op``'s data, in one direction, once:

* ``file.write``: Lemma stages the chunk and the op names it,
  ``{"transfer": {"id", "size", "sha256"}}``. The host ``GET``s it, checks the
  size and digest, and writes it.
* ``file.read``: the op names an empty slot, ``{"transfer": {"id",
  "max_bytes"}}``. The host ``PUT``s what it read, and answers with
  ``{"eof", "size", "sha256"}`` instead of ``data``.

A transfer belongs to the host it was made for (the host secret in
``Authorization`` decides which host is asking), is used once, and expires a
minute after the op's own deadline.

**Blobs** carry a run event's attachment -- an image an agent produced. The
host ``PUT``s the bytes under the run and their SHA-256 first, then the event
names them, ``{"type": "image", "mimeType", "lemma_blob": {"sha256",
"size"}}``, where it used to carry ``data`` inline. A blob is kept as long as
the run's event stream is, because the event is read when the stream is.

**Staged bodies** carry what Lemma would otherwise *send* past the ceiling: a
tool result with images in an ``mcp_ok``, a command whose payload is too large
to share a frame. The part is replaced by ``{"lemma_staged": {"id", "size",
"sha256"}}``, and the host ``GET``s the JSON from the same transfer route and
puts it back before reading the frame. A staged body is reusable until it
expires, because the frame naming it may be sent again.

Everything else that rides the link is bounded instead, by
``LINK_INLINE_DATA_BYTES`` and ``LINK_EVENT_BATCH_MAX_BYTES``, and every frame
Lemma sends passes one guard (``LinkWriter.send_frame``).
"""

from __future__ import annotations

import hashlib
import re

from app.modules.agent.domain.agent_host_link import OP_MAX_DATA_BYTES

#: Where a host fetches or delivers one transfer.
TRANSFER_PATH = "/agent-host/transfers/{transfer_id}"

#: Where a host delivers a run event's attachment before the event names it.
BLOB_PATH = "/agent-host/runs/{run_id}/blobs/{sha256}"

#: The header both sides put the bytes' SHA-256 in: lowercase hex, no prefix.
DIGEST_HEADER = "x-lemma-sha256"

#: The key an event's content block names a blob under, in place of ``data``
#: (an ``image`` block) or ``blob`` (a ``resource`` block's ``resource``).
BLOB_FIELD = "lemma_blob"

#: The most one transfer carries: one ``file.read`` or ``file.write`` chunk.
TRANSFER_MAX_BYTES = OP_MAX_DATA_BYTES

#: The most one blob carries. The same ceiling the event's image had inline.
BLOB_MAX_BYTES = 5 * 1024 * 1024

#: How long a transfer outlives the op it was made for.
TRANSFER_GRACE_SECONDS = 60

#: How long a blob is kept: the run's event stream's own lifetime, since the
#: event naming it is read from there (``event_stream._STREAM_TTL_SECONDS``).
BLOB_TTL_SECONDS = 24 * 60 * 60

#: The key a staged part of an outbound frame is replaced by.
STAGED_FIELD = "lemma_staged"

#: The most one staged body carries. A tool result is capped well below this
#: by the host's MCP bridge (8 MiB); this only bounds a broken one.
STAGED_BODY_MAX_BYTES = 32 * 1024 * 1024

#: A tool result or command payload past this, encoded, is staged rather than
#: sent inline: half the message ceiling, so the frame carrying the rest --
#: and the other commands packed beside it -- always fits.
LINK_OUTBOUND_INLINE_BYTES = 512 * 1024

#: The most commands one frame packs, encoded, leaving room for its envelope.
LINK_OUTBOUND_FRAME_BYTES = 960 * 1024

#: How long a staged tool result waits for the host: the bridge fetches it as
#: soon as the answer arrives, so this only has to outlast a slow network.
STAGED_RESULT_TTL_SECONDS = 10 * 60

#: How long a staged command payload outlives the command's own expiry.
STAGED_COMMAND_GRACE_SECONDS = 60

#: The most binary data an ``op`` still carries inline, before base64:
#: ``process.read`` output, ``process.input``, ``secret.deliver`` and
#: ``process.start``'s ``initial_input``. Small enough that base64 and the
#: envelope stay far inside the 1 MiB message ceiling.
LINK_INLINE_DATA_BYTES = 256 * 1024

#: The most one ``events`` frame carries, encoded, as the host bounds it. Half
#: the message ceiling, so a batch is never the frame that reaches it.
LINK_EVENT_BATCH_MAX_BYTES = 512 * 1024

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value) is not None


__all__ = [
    "LINK_OUTBOUND_FRAME_BYTES",
    "LINK_OUTBOUND_INLINE_BYTES",
    "STAGED_BODY_MAX_BYTES",
    "STAGED_COMMAND_GRACE_SECONDS",
    "STAGED_FIELD",
    "STAGED_RESULT_TTL_SECONDS",
    "BLOB_FIELD",
    "BLOB_MAX_BYTES",
    "BLOB_PATH",
    "BLOB_TTL_SECONDS",
    "DIGEST_HEADER",
    "LINK_EVENT_BATCH_MAX_BYTES",
    "LINK_INLINE_DATA_BYTES",
    "TRANSFER_GRACE_SECONDS",
    "TRANSFER_MAX_BYTES",
    "TRANSFER_PATH",
    "is_sha256_hex",
    "sha256_hex",
]
