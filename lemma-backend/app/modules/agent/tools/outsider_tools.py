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
        # MESSAGING -- only flagging the channel for a member to step in.
        "ask_a_person",
        # CONTACT -- offered only on a contact's run (``tools/contact_tools``):
        # the contact's own rows of a contact-owned table, and functions the
        # pod opened to contacts. Both take the contact from the run, never
        # from the model.
        "contact_records",
        "contact_function",
        # FORM -- offered only to a web visitor's run (``tools/form_tools``):
        # fills fields of a form on their page; writes nothing.
        "fill_form",
        # GROUP -- offered only to a guest's run in a group
        # (``tools/group_history``): the group's own lines, as the guest may
        # read them, from the conversation's place, never one the model names.
        "group_history",
    }
)

#: Tools a stranger's run never gets, though their toolset is kept, and why.
OUTSIDER_TOOLS_WITHHELD: dict[str, str] = {
    "web_fetch": "fetches inside the conversation owner's sandbox",
    "pod_write_record": "writes; a stranger's run is read-only",
    "pod_write_file": "writes; a stranger's run is read-only",
    "pod_edit_file": "writes; a stranger's run is read-only",
    "list_pod_members": "the directory is not the stranger's to read",
    "list_teammates": "lists the owner's other pods, which are not the stranger's to see",
    "ask_teammate": "another pod would answer with the owner's access there",
    "channel_post": "writes as the pod to its members and channels",
    "channel_read": "reads the pod's channels, which are the members'",
    "channel_search": "searches the pod's channels, which are the members'",
    "channel_list": "lists the pod's channels, which are the members'",
}


def outsider_may_call(tool_name: str) -> bool:
    """Whether a run answering somebody outside the pod may call this tool."""
    return tool_name in OUTSIDER_TOOL_NAMES
