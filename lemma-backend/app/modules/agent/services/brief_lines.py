"""How the runtime brief reads: the rendering half, with no IO in it.

``agent_context_brief`` decides what to read; this decides what the agent sees.
Separated because the two change for different reasons -- a new inventory
section is a new query, while "say which columns are required" is a new line --
and because keeping both in one file put it over the size the architecture gate
allows.

Three of these exist because of what the brief used to leave out. A table line
that said only ``name:type`` hid whether a column was required, system-managed
or a foreign key, and hid ``enable_rls`` entirely -- so an agent told to land
durable state in a table could not tell whether the one it just made was the
team's ledger or one person's private notebook. And every run read identically,
whether a person was typing or a schedule had fired at six in the morning.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.infrastructure.context_brief_repository import UserProfile

#: Columns past this are named as a count rather than listed.
MAX_COLUMNS = 40
#: Entries past this in any one listing are named as a count instead.
MAX_RESOURCES = 50


def more_note(shown: int, total: object, noun: str) -> list[str]:
    """One line saying what the cap left out, or nothing when it left nothing.

    Every cap in this brief used to be silent, so a pod's 51st table simply did
    not exist as far as the agent was concerned -- and an agent that believes a
    table is absent does not go looking for it, it tells the user there isn't
    one.
    """
    # A repository that does not count returns None rather than a total; that is
    # "unknown", not "nothing more", and must not crash prompt assembly.
    if not isinstance(total, int) or total <= shown:
        return []
    return [
        (
            f"- … and {total - shown} more {noun} not listed here "
            f"(showing {shown}). Use your tools to list them all."
        )
    ]


def with_run_framing(brief: str, *, conversation) -> str:
    """Say who started this run, and whether anybody is waiting on it.

    Every run read identically before this: a person typing in the web UI, a
    message arriving from Slack, and a schedule firing at six in the morning all
    produced the same prompt. So an unattended run would call `ask_user` and
    hang on an answer nobody was going to give, and would end by addressing a
    reply to a reader who does not exist.

    Only what is actually stamped on the conversation is reported. A run with no
    marker gets no line rather than a guess, because "a person is waiting" is
    exactly the assumption that was already doing the damage -- and it is the
    right default for the overwhelmingly common case of somebody typing.
    """
    metadata = getattr(conversation, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}

    if str(metadata.get("started_by") or "").upper() == "SCHEDULE":
        name = metadata.get("schedule_name")
        named = f" (`{name}`)" if name else ""
        return (
            f"{brief}\n\n## This run\n"
            f"- A schedule{named} started this. **Nobody is waiting on it** — "
            "`ask_user` has no one to ask and will not come back, and anything "
            "you say only in a reply will not be read.\n"
            "- Put what you did and what needs a person's decision somewhere "
            "durable: a row, a file, or a message to whoever owns it."
        )

    platform = metadata.get("surface_platform")
    if platform:
        return (
            f"{brief}\n\n## This run\n"
            f"- This arrived from {str(platform).lower()}, and the person is "
            "waiting there. Your reply goes back to the same conversation."
        )
    return brief


def user_lines(profile: UserProfile, user_id: UUID) -> list[str]:
    """Who the agent is talking to, and what time it is where they are.

    Both halves used to be missing, and neither is recoverable from anywhere
    else in the prompt. The brief named an address and a UUID, so an agent
    asked to greet somebody by name had nothing to read one from -- it either
    said the email address out loud or hoped a past agent had written the name
    into `/me`. And the only clock a run is given is UTC, which is the wrong
    answer to "this morning" and the wrong date to write into a memory file.

    Said plainly when the timezone is unset, rather than left out: an agent
    told nothing assumes the clock in front of it is the person's.
    """
    identity = profile.email or "(unknown)"
    if profile.display_name:
        identity = (
            f"{profile.display_name} <{profile.email}>"
            if profile.email
            else profile.display_name
        )
    lines = [f"- User: {identity} ({user_id})"]
    if profile.timezone:
        lines.append(
            f"- Their timezone: {profile.timezone}. The clock you are given "
            "reads UTC — convert before naming a time of day or resolving a "
            "date for them."
        )
    else:
        lines.append(
            "- Their timezone is not set, and the clock you are given reads "
            "UTC, which may not be theirs. Don't name a time of day or resolve "
            '"today" on their behalf without asking.'
        )
    return lines


def column_spec(column) -> str:
    """One column, with the four facts that change what a write looks like.

    It used to be ``name:type`` and nothing else, which loses all four: an agent
    could not see that a column was required (so it wrote rows that came back
    rejected), that one was system-managed (so it tried to set ``user_id`` and
    ``created_at`` itself), which columns were foreign keys (so it could not see
    how two tables related), or what the builder's own description said -- the
    one place a pod explains its data model in words.
    """
    type_name = getattr(column.type, "value", column.type)
    marks: list[str] = []
    if getattr(column, "system", False) or getattr(column, "auto", False):
        marks.append("auto")
    elif getattr(column, "required", False):
        marks.append("required")
    if getattr(column, "unique", False):
        marks.append("unique")
    fk = getattr(column, "foreign_key", None)
    references = getattr(fk, "references", None) if fk is not None else None
    if references:
        marks.append(f"→{references}")
    spec = f"{column.name}:{type_name}"
    if marks:
        spec += "(" + " ".join(marks) + ")"
    description = (getattr(column, "description", None) or "").strip()
    if description:
        spec += f' "{description}"'
    return spec


def table_line(table) -> str:
    shown = table.columns[:MAX_COLUMNS]
    columns = ", ".join(column_spec(column) for column in shown)
    # A column the agent cannot see is a column it will omit from a write and
    # then be told is required, or will report to the user as not existing.
    hidden = len(table.columns) - len(shown)
    suffix = (
        f" (+{hidden} more columns — describe the table to see them)" if hidden else ""
    )
    # Who can see the rows, which is the fact the brief was most obviously
    # missing: `enable_rls` defaults to on, so a table created without a thought
    # about it is private per person. An agent told to land durable state in a
    # table, and not told this, builds the team's ledger as somebody's private
    # notebook and reports that the team can now see it.
    scope = (
        "per-user rows (RLS on — each member sees only their own)"
        if getattr(table, "enable_rls", True)
        else "shared rows (RLS off — everyone sees the same rows)"
    )
    return (
        f"- {table.table_name} (pk: {table.primary_key_column}, {scope}): "
        f"{columns}{suffix}"
    )


def top_level_file_entries(tree: object) -> list[str]:
    if not isinstance(tree, dict):
        return []
    children = tree.get("children")
    if not isinstance(children, list):
        return []
    entries: list[str] = []
    for child in children[:MAX_RESOURCES]:
        if isinstance(child, dict):
            name = child.get("path") or child.get("name")
            kind = child.get("kind") or child.get("type")
            if name:
                entries.append(f"{name}" + (f" [{kind}]" if kind else ""))
    if len(children) > MAX_RESOURCES:
        entries.append(
            f"… and {len(children) - MAX_RESOURCES} more top-level entries "
            "not listed here"
        )
    return entries
