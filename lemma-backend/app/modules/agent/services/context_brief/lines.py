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

from app.modules.agent.domain.value_objects import ConversationOrigin, RunTrigger
from typing import TYPE_CHECKING

from uuid import UUID

from app.modules.agent.config import agent_settings

from app.modules.agent.infrastructure.context_brief_repository import UserProfile

if TYPE_CHECKING:
    from app.modules.datastore.contracts import ColumnSchema

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


#: Triggers known to start a run without a person: a timer coming due, a
#: schedule firing.
#:
#: An allow-list of *human* triggers was the first shape of this and it was the
#: wrong way round. A resume -- somebody clicked approve, or answered
#: ``ask_user`` -- was missing from it, so the run that exists precisely because
#: a person had just responded was told nothing had come from a person. Listing
#: the automatic ones instead means an unrecognised trigger is never called
#: unattended, and wrongly claiming nobody is watching is the failure that
#: actually costs somebody an answer.
AUTOMATIC_TRIGGERS = frozenset({RunTrigger.WAKE, RunTrigger.SCHEDULE_FIRE})


def _with_budget(framing: str, *, unattended: bool) -> str:
    """Append the backstop this run stops at, and say it is not a deadline.

    Nothing otherwise states a limit, while the base prompts say to complete
    the work -- so a run could only discover the ceiling by hitting it. Saying
    it up front is worth the tokens, but *how* it is said decides what the run
    does with it: given a number and no framing, a model treats it as a target
    and rushes. It is not one. The work should take as long as it takes, and
    these sit far enough out that only a run going in circles arrives.

    The numbers are the same ones enforced in the loop, read from the same
    settings, so the brief cannot drift from the behaviour.
    """
    steps = agent_settings.agent_run_budget_model_requests
    seconds = (
        agent_settings.agent_run_budget_unattended_wall_clock_seconds
        if unattended
        else agent_settings.agent_run_budget_wall_clock_seconds
    )
    if steps <= 0 and seconds <= 0:
        return framing

    limits = []
    if steps > 0:
        limits.append(f"{steps} steps")
    if seconds > 0:
        limits.append(f"{int(seconds // 60)} minutes")
    backstop = (
        f"As a backstop, past roughly {' or '.join(limits)} this run pauses "
        "and asks a person whether to carry on, and you will be told before you "
        "are near it."
    )
    if unattended:
        return (
            f"{framing}\n"
            "- Take the time the work needs; a long run that returns the thing "
            f"asked for is a good outcome. {backstop}"
        )
    # Attended: somebody is looking at the screen until this reply lands. The
    # time goes on extra steps -- a check that re-derives what a tool already
    # reported, a render to confirm markup that was just read back -- far more
    # than on the work, so this names those rather than setting a clock the
    # model would race.
    return (
        f"{framing}\n"
        "- A person is waiting for this reply. Do the smallest thing that fully "
        "answers it: act on what a tool already told you instead of checking it "
        "again, verify what you wrote by reading it back rather than rendering "
        "or screenshotting it, and do not load a skill for something this brief "
        "or the conversation already explains. Work that is genuinely large "
        f"still gets the time it needs. {backstop}"
    )


def run_is_unattended(run_trigger: RunTrigger | None) -> bool:
    """Whether nobody is expected to be watching this turn.

    The claim has to be positively established, never inferred from absence:
    anything unrecognised is treated as attended, because wrongly deciding
    nobody is watching is the failure that costs somebody an answer.

    Read by two callers who must agree. The brief tells the model not to expect
    a quick reply; the run budget uses a longer ceiling, because nobody is
    waiting — and still a ceiling, because nobody is watching the spend either.
    """
    return run_trigger is None or run_trigger in AUTOMATIC_TRIGGERS


def run_trigger_of(agent_run: object) -> RunTrigger | None:
    """What kicked off this run.

    Lives here rather than with the runner because the only thing that reads it
    is the framing below, and the pair is one fact: a schedule opens the
    *conversation* once, so the conversation cannot say whether a person is here
    on this turn. Only the run can.
    """
    state = getattr(agent_run, "state", None)
    trigger = getattr(state, "trigger", None)
    return trigger if isinstance(trigger, RunTrigger) else None


def call_brief(conversation) -> str:
    """The coordinator's brief, while a live call is held in this conversation.

    Per run rather than in the conversation's instructions: a call can happen
    in any chat, and the chat must read as itself again once the call ends.
    """
    from app.modules.agent.domain.calls import CallMark, coordinator_brief

    mark = getattr(conversation, "call", None)
    live = isinstance(mark, CallMark) and mark.live_coordinator
    return coordinator_brief() if live else ""


def run_framing(
    conversation,
    *,
    run_trigger: RunTrigger | None = None,
    platform: str | None = None,
) -> str:
    """Say what started **this run**, and whether anybody is waiting on it.

    Changes from run to run, so it goes in the run's context part rather than
    the system prompt (``domain/run_context_prompt``).

    Every run read identically before this: a person typing, a message from
    Slack, and a schedule firing at six in the morning all produced the same
    prompt, so an unattended run would call `ask_user` and hang on an answer
    nobody was going to give.

    Two corrections since. The first version read only the conversation --
    a schedule opens it and it stays a schedule's, so a person opening that
    conversation the next morning was still told nobody would read the reply.
    The second used an allow-list of human sources and left a resume out of it,
    which meant the run that exists *because* somebody had just approved
    something was told nothing had come from a person.

    So: the conversation's ``origin`` says how it began, ``run_trigger`` says
    what started this turn, and the unattended claim needs the trigger to be
    positively known automatic. Anything unrecognised is not called unattended. ``platform`` is
    the one the run talks over (``surface_context_for_run``), never the
    conversation's: a member's conversation in a channel was last heard on
    WhatsApp as often as not when they ask in Lemma.
    """
    metadata = getattr(conversation, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    started_by_schedule = (
        getattr(conversation, "origin", None) is ConversationOrigin.SCHEDULE
    )
    unattended = run_is_unattended(run_trigger)
    where = f" from {platform.lower()}" if platform else ""

    if run_trigger is RunTrigger.RESUME:
        return _with_budget(
            "## This run\n"
            "- A person answered what you were waiting on — an approval, or a "
            "reply to `ask_user`. Their answer is in your history above. Carry "
            "on from where you paused, and tell them what happened.",
            unattended=False,
        )

    if started_by_schedule and unattended:
        name = metadata.get("schedule_name")
        named = f" (`{name}`)" if name else ""
        return _with_budget(
            "## This run\n"
            f"- A schedule{named} started this, and nothing since has come from "
            "a person. **Assume nobody is watching right now** — `ask_user` "
            "pauses the run until somebody answers, which on an unattended "
            "firing may be a long time.\n"
            "- Your reply is still saved to this conversation and a person can "
            "read it later. Put anything that needs a decision where its owner "
            "will find it — a row, a file, or a message to them — rather than "
            "only in the reply.",
            unattended=True,
        )

    if started_by_schedule:
        return _with_budget(
            "## This run\n"
            "- This conversation was started by a schedule, and a person is now "
            f"asking in it{where}. Answer them here.",
            unattended=False,
        )

    if platform:
        return _with_budget(
            "## This run\n"
            f"- This arrived{where}, and the person is waiting there. Your reply "
            "goes back to the same conversation.",
            unattended=False,
        )
    return _with_budget("## This run", unattended=False)


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


def column_spec(column: ColumnSchema) -> str:
    """Write constraints only; full descriptions are available on inspection."""
    # Imported here: the datastore contracts pull in its API schemas, and the
    # brief is imported on paths that never render a table.
    from app.modules.datastore.contracts import describe_column

    return describe_column(column).brief()


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
