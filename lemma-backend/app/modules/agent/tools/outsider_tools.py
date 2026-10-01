"""Every tool a run answering somebody outside the pod may call, by name.

The toolsets such a run keeps (``domain/outsiders.OUTSIDER_TOOLSETS``) are a
first cut, and not a safe one on their own: a toolset is a bundle, and a tool
added to it later arrives in every stranger's run without anybody deciding it
should. ``web_fetch`` arrived that way -- it rides in WEB_SEARCH beside
``web_search``, and it opens the conversation owner's sandbox.

So the question is asked of each tool. A name here was read and found to reach
nothing but what the pod made Public (or, for messaging, nobody but the member
who looks after the conversation); a name in ``OUTSIDER_TOOLS_WITHHELD`` was
read and refused, with the reason. Anything in neither is refused too, by the
harness gate (``capabilities/outsider_gate``) and the dispatcher, and
``test_outsider_tool_allowlist`` fails until somebody classifies it.
"""

from __future__ import annotations

#: Tools a stranger's run may call. Each goes through the anonymous authorizer
#: (``tool_authorization_context``), reaches no sandbox, and acts as nobody.
OUTSIDER_TOOL_NAMES = frozenset(
    {
        # POD -- reads only; the anonymous context allows `.read` on Public.
        "pod_tables",
        "pod_get_records",
        "pod_query",
        "pod_list_files",
        "pod_read_file",
        "pod_view_document_pages",
        "pod_get_file_url",
        "pod_search_files",
        # WEB_SEARCH -- a search API call; no pod or owner identity.
        "web_search",
        # MESSAGING -- fenced to the member who looks after the conversation,
        # and to notifications this conversation sent.
        "message_user",
        "check_messages",
    }
)

#: Tools a stranger's run never gets, though their toolset is kept, and why.
OUTSIDER_TOOLS_WITHHELD: dict[str, str] = {
    "web_fetch": "fetches inside the conversation owner's sandbox",
    "pod_write_record": "writes; a stranger's run is read-only",
    "pod_write_file": "writes; a stranger's run is read-only",
    "pod_edit_file": "writes; a stranger's run is read-only",
    "list_pod_members": "the directory is not the stranger's to read",
}


def outsider_may_call(tool_name: str) -> bool:
    """Whether a run answering somebody outside the pod may call this tool."""
    return tool_name in OUTSIDER_TOOL_NAMES
