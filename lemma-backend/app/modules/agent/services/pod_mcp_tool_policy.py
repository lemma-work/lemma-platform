"""How each pod tool presents itself over MCP, and what it needs to be called.

Two things an outside client needs that the Agent Host never did:

* **Annotations.** Claude and ChatGPT both read ``readOnlyHint`` and
  ``destructiveHint`` to decide whether to ask the person before a call, and
  both document them as required of a connector. The MCP defaults are the
  cautious ones -- not read-only, destructive, open-world -- so an unannotated
  read would be confirmed every time.
* **Scopes.** A client connected with ``pod:read`` only is not shown the
  writing tools, and is refused if it calls one anyway.

The table is by name and total: `test_pod_mcp_tool_policy` fails when a tool is
added to any toolset the server serves (`POD_MCP_TOOLSETS`) without a row here. A missing row falls back to the
most cautious policy -- a destructive write -- which is safe but wrong for a
tool that only reads: read-only connections would not be offered it, and every
client would ask the person before each call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mcp.types import ToolAnnotations

from app.modules.mcp_access.contracts import Scope


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    title: str
    scope: Scope
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False

    def annotations(self) -> ToolAnnotations:
        read_only = self.scope is Scope.READ
        return ToolAnnotations(
            title=self.title,
            read_only_hint=read_only,
            destructive_hint=self.destructive,
            idempotent_hint=self.idempotent,
            # Everything these tools touch is inside the pod, save a public
            # file link, which anyone it is handed to can open.
            open_world_hint=self.open_world,
        )


POD_TOOL_POLICIES: dict[str, ToolPolicy] = {
    "pod_tables": ToolPolicy("List tables", Scope.READ),
    "pod_get_records": ToolPolicy("Read records", Scope.READ),
    "pod_query": ToolPolicy("Query tables with SQL", Scope.READ),
    "pod_list_files": ToolPolicy("List files", Scope.READ),
    "pod_read_file": ToolPolicy("Read a file", Scope.READ),
    "pod_search_files": ToolPolicy("Search files", Scope.READ),
    "pod_view_document_pages": ToolPolicy("View document pages", Scope.READ),
    # Listed for reading: an in-app link needs the person's own session to
    # open. Its public mode is `scope_for_call`'s exception.
    "pod_get_file_url": ToolPolicy("Get a link to a file", Scope.READ, open_world=True),
    # Can delete a record, so destructive; not idempotent, since "create" run
    # twice makes two rows.
    "pod_write_record": ToolPolicy(
        "Create, update or delete a record",
        Scope.WRITE,
        destructive=True,
        idempotent=False,
    ),
    # Can overwrite an existing file. Writing the same content twice leaves the
    # same file, so it is idempotent.
    "pod_write_file": ToolPolicy("Write a file", Scope.WRITE, destructive=True),
    # Replaces text in place. Not idempotent: run twice, the second finds its
    # target already replaced and fails, or matches the new text and edits again.
    "pod_edit_file": ToolPolicy(
        "Edit a file", Scope.WRITE, destructive=True, idempotent=False
    ),
    # A decision is a log entry, not a change to the pod's data, so asking is
    # reading. Not idempotent: without a subject, each call is asked afresh and
    # recorded again, and a model's answer may differ the second time.
    "decide": ToolPolicy("Decide", Scope.READ, idempotent=False),
    # Records nothing at all.
    "test_decider": ToolPolicy("Try a decider", Scope.READ),
    # Saving an existing name adds a version and keeps the old ones, so it is
    # additive rather than destructive -- but every save is a new version.
    "define_decider": ToolPolicy("Save a decider", Scope.WRITE, idempotent=False),
    # Replaces a machine's answer when it corrects one.
    "answer_decision": ToolPolicy("Answer a decision", Scope.WRITE, destructive=True),
}

_WRITE_UNLESS_KNOWN = ToolPolicy(
    "Pod tool", Scope.WRITE, destructive=True, idempotent=False
)


def policy_for(tool_name: str) -> ToolPolicy:
    """The tool's row, or the most cautious one for a tool with none."""
    return POD_TOOL_POLICIES.get(tool_name, _WRITE_UNLESS_KNOWN)


def scope_for_call(tool_name: str, arguments: Mapping[str, object] | None) -> Scope:
    """The scope this particular call needs.

    The tool's own, except a public file link: it mints a URL anyone can open,
    for up to a week and a thousand downloads, and it outlives the connection
    that made it. That is publishing, not reading, so it needs ``pod:write``.
    """
    if tool_name == "pod_get_file_url" and _url_type(arguments) == "public":
        return Scope.WRITE
    if tool_name == "decide" and _decides_rows(arguments):
        # Past a few rows the results land in a pod file, so a batch writes.
        return Scope.WRITE
    return policy_for(tool_name).scope


def _decides_rows(arguments: Mapping[str, object] | None) -> bool:
    source = _call_arguments(arguments)
    return any(source.get(key) is not None for key in ("items", "file", "table"))


def _call_arguments(arguments: Mapping[str, object] | None) -> Mapping[str, object]:
    if not arguments:
        return {}
    inner = arguments.get("request")
    return inner if isinstance(inner, Mapping) else arguments


def _url_type(arguments: Mapping[str, object] | None) -> object:
    if not arguments:
        return None
    # The tool's one parameter is its request model; clients send it wrapped
    # under its name or, when the schema is inlined, flat.
    inner = arguments.get("request")
    source = inner if isinstance(inner, Mapping) else arguments
    return source.get("url_type")


APPROVAL_KEYS = ("needs_approval", "approval")


def without_approval_envelope(payload: object) -> object:
    """A pod tool result without its ``request_approval`` hand-off.

    A denied write tells a Lemma agent to re-issue the call through
    ``request_approval``, a tool only Lemma agents have. An outside client acts
    as the person, so a denial is the answer rather than a question to escalate,
    and the envelope would send the model looking for a tool that is not there.
    """
    if not isinstance(payload, dict) or not any(
        key in payload for key in APPROVAL_KEYS
    ):
        return payload
    return {key: value for key, value in payload.items() if key not in APPROVAL_KEYS}
