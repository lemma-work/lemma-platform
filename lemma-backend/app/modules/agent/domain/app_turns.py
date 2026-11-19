"""Which pod app's page sent a turn into a run.

An app's page acts as its viewer, so a turn it sends to a pod agent starts
(or joins) that viewer's run, which uses the viewer's own machine like every
run they own. That is deliberate today; the app is recorded on the run so the
rule can be narrowed later -- keeping, say, the browser's logins from such a
run -- without guessing which runs it would have applied to. Nothing reads
it yet.
"""

from __future__ import annotations

#: The ``agent_runs.run_metadata`` key holding the app's id. Written by the
#: server from the request's app pin (``core/app_pin``), never by a client.
SENT_FROM_APP_KEY = "sent_from_app"

__all__ = ["SENT_FROM_APP_KEY"]
