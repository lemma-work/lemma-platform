"""The Agent Host: Lemma runs on somebody's own machine, over one WebSocket.

docs/architecture/agent-host.md is the protocol. Inside:

* ``domain/`` -- the wire models, link frames, permissions, readiness,
  selections, transfers and ops;
* ``link/`` -- the server's half of the link, one session per connection;
* ``control/`` -- pairing, dispatch, admission, recovery, and what the
  sub-package keeps per conversation and per run;
* ``harness/`` -- ``RemoteHarness``, which turns one run's event stream back
  into an agent run;
* ``mcp.py`` -- the conversation's tools, served to the host over the link;
* ``controllers/`` -- the HTTP routes, registered by the agent module;
* ``api.py`` -- the only file the rest of the agent module imports.
"""
