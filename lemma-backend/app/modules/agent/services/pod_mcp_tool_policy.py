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
added to the pod toolset without a row here, because a missing row would
default a new writing tool to "read" and hand it to read-only connections.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.types import ToolAnnotations

from app.modules.mcp_access.contracts import Scope


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    title: str
    scope: Scope
    destructive: bool = False
    idempotent: bool = True

    def annotations(self) -> ToolAnnotations:
        read_only = self.scope is Scope.READ
        return ToolAnnotations(
            title=self.title,
            read_only_hint=read_only,
            destructive_hint=self.destructive,
            idempotent_hint=self.idempotent,
            # Everything these tools touch is inside the pod. A signed file URL
            # is a link to the pod's own storage, not a reach outside it.
            open_world_hint=False,
        )


POD_TOOL_POLICIES: dict[str, ToolPolicy] = {
    "pod_tables": ToolPolicy("List tables", Scope.READ),
    "pod_get_records": ToolPolicy("Read records", Scope.READ),
    "pod_query": ToolPolicy("Query tables with SQL", Scope.READ),
    "pod_list_files": ToolPolicy("List files", Scope.READ),
    "pod_read_file": ToolPolicy("Read a file", Scope.READ),
    "pod_search_files": ToolPolicy("Search files", Scope.READ),
    "pod_view_document_pages": ToolPolicy("View document pages", Scope.READ),
    "pod_get_file_url": ToolPolicy("Get a link to a file", Scope.READ),
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
}

_WRITE_UNLESS_KNOWN = ToolPolicy(
    "Pod tool", Scope.WRITE, destructive=True, idempotent=False
)


def policy_for(tool_name: str) -> ToolPolicy:
    """The tool's row, or the most cautious one for a tool with none."""
    return POD_TOOL_POLICIES.get(tool_name, _WRITE_UNLESS_KNOWN)


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
