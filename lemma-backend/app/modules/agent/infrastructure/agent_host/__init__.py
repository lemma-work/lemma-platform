"""The Agent Host control plane: pairing, dispatch, and what came back.

An Agent Host is somebody's machine, running Codex or Claude Code, paired to
this workspace. This package is everything about *managing* one: admitting a
host that has just connected, minting the commands it executes, tracking which
run went to which harness, taking in the events it reports, and recovering the
runs it abandoned when it went offline.

The run consumer is deliberately somewhere else -- `harnesses.agent_host` --
because turning a host's event stream into one agent run is a different job with
a different failure mode. This side answers "which hosts exist, are they
healthy, and what did we send them"; that side answers "what did this run say".

A host is not trusted infrastructure. It is a laptop that can vanish mid-run,
reconnect with a stale token, or report events for a run that has already been
finalized -- so admission, remint and recovery are the three things worth
reading first.
"""
