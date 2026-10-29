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

**Anything not listed is UNSAFE.** An undeclared tool gets an honest "outcome
unknown" instead of a silent duplicate, which is the failure that matters. The
registry is total over the static toolsets (``test_replay_registry``), so a new
tool has to be decided rather than defaulted by accident.

Pausing tools (``ask_user``, ``request_approval``, ``browser_sign_in``,
``wait_for``) are not here: an open pause is the conversation waiting on
somebody, resolved by the approval and wait machinery, never replayed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum

from app.modules.agent.domain.pausing_tools import PAUSING_TOOL_NAMES


class ReplayClass(StrEnum):
    SAFE = "SAFE"
    KEYED = "KEYED"
    UNSAFE = "UNSAFE"


_SAFE = ReplayClass.SAFE
_KEYED = ReplayClass.KEYED
_UNSAFE = ReplayClass.UNSAFE

#: Per tool name. Kept in toolset order so a reviewer can read a toolset whole.
REPLAY_CLASS: Mapping[str, ReplayClass] = {
    # Workspace shell: commands with effects nobody can see from here.
    "exec_command": _UNSAFE,
    "execute_python": _UNSAFE,
    "manage_process": _UNSAFE,
    "view_image": _SAFE,
    # Skills and the web: reads.
    "list_skills": _SAFE,
    "load_skill": _SAFE,
    "web_fetch": _SAFE,
    "web_search": _SAFE,
    # Showing a resource renders the tool's own return; nothing is sent.
    "display_resource": _SAFE,
    # Speech: listing and transcribing read; speaking writes a new file.
    "list_voices": _SAFE,
    "listen": _SAFE,
    "say": _UNSAFE,
    # The pod: reads, and record writes refined by operation below. Writing a
    # file or editing one in place may overwrite a change made since.
    "pod_tables": _SAFE,
    "pod_get_records": _SAFE,
    "pod_query": _SAFE,
    "pod_list_files": _SAFE,
    "pod_read_file": _SAFE,
    "pod_search_files": _SAFE,
    "pod_view_document_pages": _SAFE,
    "pod_get_file_url": _SAFE,
    "pod_write_file": _UNSAFE,
    "pod_write_record": _KEYED,
    "pod_edit_file": _UNSAFE,
    # Replaces what the pod owns wholesale, so run again it can undo what a
    # person set since, as a rewritten file can.
    "set_what_you_own": _UNSAFE,
    # Sub-agents: a spawn is unique on (parent run, call); interacting is
    # refined by action below.
    "spawn_subagent": _KEYED,
    "query_subagents": _SAFE,
    "interact_subagent": _UNSAFE,
    # Connectors: discovery reads; running an operation is a write unless the
    # catalog says otherwise, and it does not tell this module the method.
    "list_connectors": _SAFE,
    "search_connector_operations": _SAFE,
    "describe_connector_operation": _SAFE,
    # Hands back a sign-in link; asked twice, the same one.
    "connect_connector": _SAFE,
    "run_connector_operation": _UNSAFE,
    # Messaging: sends key on the run and call; the rest read.
    "channel_post": _KEYED,
    "ask_teammate": _UNSAFE,
    "channel_read": _SAFE,
    "channel_search": _SAFE,
    "channel_list": _SAFE,
    "list_pod_members": _SAFE,
    "list_teammates": _SAFE,
    # Answering a question put to the member (a message, a workflow form): a
    # question is answered once, so the replayed call is refused and the first
    # answer is the one recorded.
    "answer_question": _KEYED,
    "ask_a_person": _KEYED,
    # Contacts and the visitor's form.
    "contact_records": _SAFE,
    "contact_function": _KEYED,
    "fill_form": _SAFE,
    # In-run bookkeeping: the todo list is replaced whole; tool search reads.
    "write_todos": _SAFE,
    "search_tools": _SAFE,
    # The output tool ends the run. Re-asked rather than replayed: its return
    # is the run's own answer, which a resumed run writes for itself.
    "final_answer": _UNSAFE,
}

#: Per-call refinements, for tools whose class depends on what they were asked.
Refinement = Callable[[Mapping[str, object]], ReplayClass]


def _request(args: Mapping[str, object]) -> Mapping[str, object]:
    """The call's request body -- tools take it bare or under ``request``."""
    inner = args.get("request")
    return inner if isinstance(inner, Mapping) else args


def _record_write(args: Mapping[str, object]) -> ReplayClass:
    """A create is keyed on the call; anything else may undo a later change."""
    action = str(_request(args).get("action") or "").lower()
    return _KEYED if action == "create" else _UNSAFE


def _subagent_interaction(args: Mapping[str, object]) -> ReplayClass:
    """Awaiting or stopping a child is safe twice; sending it a message is not."""
    action = str(_request(args).get("action") or "").lower()
    return _SAFE if action in {"await", "stop"} else _UNSAFE


REFINEMENTS: Mapping[str, Refinement] = {
    "pod_write_record": _record_write,
    "interact_subagent": _subagent_interaction,
}


def is_replayable_call(tool_name: str | None) -> bool:
    """Whether a resumed turn settles this open call at all (pauses it does not)."""
    return bool(tool_name) and tool_name not in PAUSING_TOOL_NAMES


def replay_class(tool_name: str | None, args: object = None) -> ReplayClass:
    """The class of one recorded call: by its tool, refined by its arguments."""
    if not tool_name:
        return _UNSAFE
    declared = REPLAY_CLASS.get(tool_name, _UNSAFE)
    refine = REFINEMENTS.get(tool_name)
    if refine is None:
        return declared
    return refine(args if isinstance(args, Mapping) else {})


__all__ = [
    "REFINEMENTS",
    "REPLAY_CLASS",
    "ReplayClass",
    "is_replayable_call",
    "replay_class",
]
