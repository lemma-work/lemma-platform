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


#: Run sources known to start without a person: a timer coming due. A schedule's
#: own first firing records no source at all, which ``None`` covers.
#:
#: An allow-list of *human* sources was the first shape of this and it was the
#: wrong way round. ``approval_resume`` -- somebody clicked approve, or answered
#: ``ask_user`` -- was missing from it, so the run that exists precisely because
#: a person had just responded was told nothing had come from a person. Listing
#: the automatic ones instead means an unrecognised source is never called
#: unattended, and wrongly claiming nobody is watching is the failure that
#: actually costs somebody an answer.
AUTOMATIC_RUN_SOURCES = frozenset({"snooze_resume", "agent_snooze"})

#: A person answered a pause: an approval decision, or a reply to ``ask_user``.
#: Worth saying out loud, because the run resumes mid-task and the answer it was
#: waiting for is now sitting in its history.
RESUMED_BY_PERSON = "approval_resume"


def run_source_of(agent_run) -> str | None:
    """What kicked off this run, from its own metadata.

    Lives here rather than with the runner because the only thing that reads it
    is the framing below, and the pair is one fact: a schedule stamps the
    *conversation* once, so the conversation cannot say whether a person is here
    on this turn. Only the run can.
    """
    metadata = getattr(agent_run, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    source = metadata.get("source")
    return str(source) if source else None


def with_run_framing(brief: str, *, conversation, run_source: str | None = None) -> str:
    """Say what started **this run**, and whether anybody is waiting on it.

    Every run read identically before this: a person typing, a message from
    Slack, and a schedule firing at six in the morning all produced the same
    prompt, so an unattended run would call `ask_user` and hang on an answer
    nobody was going to give.

    Two corrections since. The first version read only the conversation --
    ``started_by`` is stamped there when a schedule creates it and it stays
    there, so a person opening that conversation the next morning was still told
    nobody would read the reply. The second used an allow-list of human sources
    and left ``approval_resume`` out of it, which meant the run that exists
    *because* somebody had just approved something was told nothing had come
    from a person.

    So: the conversation says how it began, ``run_source`` says what started this
    turn, and the unattended claim needs the source to be positively known
    automatic. Anything unrecognised is not called unattended.
    """
    metadata = getattr(conversation, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    started_by_schedule = str(metadata.get("started_by") or "").upper() == "SCHEDULE"
    unattended = run_source is None or run_source in AUTOMATIC_RUN_SOURCES
    platform = metadata.get("surface_platform")
    where = f" from {str(platform).lower()}" if platform else ""

    if run_source == RESUMED_BY_PERSON:
        return (
            f"{brief}\n\n## This run\n"
            "- A person answered what you were waiting on — an approval, or a "
            "reply to `ask_user`. Their answer is in your history above. Carry "
            "on from where you paused, and tell them what happened."
        )

    if started_by_schedule and unattended:
        name = metadata.get("schedule_name")
        named = f" (`{name}`)" if name else ""
        return (
            f"{brief}\n\n## This run\n"
            f"- A schedule{named} started this, and nothing since has come from "
            "a person. **Assume nobody is watching right now** — `ask_user` "
            "pauses the run until somebody answers, which on an unattended "
            "firing may be a long time.\n"
            "- Your reply is still saved to this conversation and a person can "
            "read it later. Put anything that needs a decision where its owner "
            "will find it — a row, a file, or a message to them — rather than "
            "only in the reply."
        )

    if started_by_schedule:
        return (
            f"{brief}\n\n## This run\n"
            "- This conversation was started by a schedule, and a person is now "
            f"asking in it{where}. Answer them here."
        )

    if platform:
        return (
            f"{brief}\n\n## This run\n"
            f"- This arrived{where}, and the person is waiting there. Your reply "
            "goes back to the same conversation."
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
    """Write constraints only; full descriptions are available on inspection."""
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
    # Row filtering and resource visibility are independent settings.
    rls = "on" if getattr(table, "enable_rls", True) else "off"
    visibility = str(getattr(table, "visibility", "") or "").upper()
    who = f"; visibility={visibility}" if visibility else ""
    return (
        f"- {table.table_name} (pk={table.primary_key_column}; rls={rls}{who}): "
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
