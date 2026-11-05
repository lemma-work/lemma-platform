"""What a resumed turn may do with a tool call its previous attempt left open.

A turn checkpoints at every step: each tool call and each tool return is its
own committed row. A worker that dies between the two leaves a call with no
return, and the turn resumed on another worker has to decide what that call
did. Three answers, declared per tool:

* ``SAFE`` -- reading, or bookkeeping private to the run (its todo list).
  Run it again under the recorded call id.
* ``KEYED`` -- the effect is deduplicated downstream on the recorded call id
  (a channel line's client id, a sub-agent spawn unique on the parent
  run and call, a record create keyed on the call). Run it again: the second
  run returns the first one's outcome.
* ``UNSAFE`` -- an effect nobody can deduplicate: a shell command, a browser
  action, a connector write, anything on an Agent Host or an external MCP
  server. Never run again; the model is told the outcome is unknown and to
  check the state before repeating it.

A write that sets a value -- a record update, a whole-file write -- is not
SAFE even though applying it twice gives the same row or file. Between the
first attempt and the replay a person, a workflow or another agent may have
changed that row or file, and the replay would put the old value back over
theirs. It would be safe only if the target were unchanged since the step
started, and nothing records the target's state at that point (the call row
holds the arguments alone), so these are UNSAFE: the model is told the outcome
is unknown and reads the row or file before deciding. A delete is UNSAFE for
the same reason: a table whose key is chosen by its writers may hold a new row
under that key by the time the replay runs.

Each spec declares its class on its policy (``ToolPolicy.replay``), refined by
the call's arguments where the class depends on them. **Anything undeclared is
UNSAFE**, and so is a recorded call no spec of the run answers to: an honest
"outcome unknown" instead of a silent duplicate, which is the failure that
matters.

Pausing tools (``ask_user``, ``request_approval``, ``browser_sign_in``,
``wait_for``) are not here: an open pause is the conversation waiting on
somebody, resolved by the approval and wait machinery, never replayed.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent.domain.pauses import PAUSING_CALL_NAMES
from app.modules.agent.domain.tools import ReplayClass, ToolSpec


def request_body(args: Mapping[str, object]) -> Mapping[str, object]:
    """The call's request body -- tools take it bare or under ``request``."""
    inner = args.get("request")
    return inner if isinstance(inner, Mapping) else args


def is_replayable_call(tool_name: str | None) -> bool:
    """Whether a resumed turn settles this open call at all (pauses it does not)."""
    return bool(tool_name) and tool_name not in PAUSING_CALL_NAMES


def replay_class(spec: ToolSpec | None, args: object = None) -> ReplayClass:
    """The class of one recorded call: by its tool, refined by its arguments."""
    if spec is None:
        return ReplayClass.UNSAFE
    declared = spec.policy.replay
    if isinstance(declared, ReplayClass):
        return declared
    return declared(args if isinstance(args, Mapping) else {})


__all__ = ["ReplayClass", "is_replayable_call", "replay_class", "request_body"]
