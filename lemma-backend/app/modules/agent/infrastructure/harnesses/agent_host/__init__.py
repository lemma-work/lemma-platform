"""Consuming one Agent Host run: its events, its tools, its answer.

`RemoteHarness` looks like any other harness from the runner's side -- it yields
`AgentEvent`s -- but nothing here executes anything. The work happens on
somebody else's machine and this reads the stream it sends back, turning host
events into messages, tool calls, artifacts and a final answer.

Two things make that harder than it sounds, and they are why this is a package:

* **The stream is not exactly-once.** A host can reconnect and replay, so events
  arrive twice; the run window and the event applier are what keep a duplicate
  from becoming a second message.
* **A pause here does not end the run.** Unlike the in-process harness, an ACP
  permission request leaves the run RUNNING while the host waits on the answer,
  which is why resume has a different shape on this side.

The control plane -- pairing, dispatch, recovery -- lives in
`infrastructure.agent_host`.
"""
