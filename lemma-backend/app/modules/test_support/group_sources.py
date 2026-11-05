"""A group a surface is in, as a test builds one: a shared place of a channel.

Not a test module. A group is a ``Source`` of kind CHAT (``channels``), and a
unit test needs one without a database: this fills in what a test does not
care about -- its id, its account -- the way the place's row would.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.channels.contracts import Source, SourceKind


def a_group(**fields: object) -> Source:
    """A group a surface is in; ``fields`` as the test needs them."""
    defaults: dict[str, object] = {
        "id": uuid4(),
        "channel_id": uuid4(),
        "pod_id": uuid4(),
        "account_id": uuid4(),
        "surface_id": uuid4(),
        "platform": "TELEGRAM",
        "kind": SourceKind.CHAT,
    }
    return Source.model_validate({**defaults, **fields})
