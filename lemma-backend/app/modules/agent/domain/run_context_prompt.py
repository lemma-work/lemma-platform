"""What changes from one run to the next, kept out of the system prompt.

A provider caches the request prefix: the system prompt, then the replayed
history. Anything in the system prompt that changes between two runs of one
conversation -- a task list after `write_todos`, a memory write, an edit to the
attached doc, an answered notification, a new file -- invalidates the cache for
the whole history behind it, so the first request of the next run re-reads the
entire conversation.

So that material is rendered here, once per run, into one user-role part that
rides next to the current turn and is never stored as history. The system
prompt and the replayed history then stay byte-identical from run to run, and
what changes is read once, at the end, where it costs only its own length.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.modules.agent.domain.value_objects import AgentToolset

if TYPE_CHECKING:
    from app.modules.agent.domain.entities import Conversation

RUN_CONTEXT_OPEN = "<run_context>"
RUN_CONTEXT_CLOSE = "</run_context>"


def render_run_context(
    *, sections: Iterable[str | None], now: datetime | None = None
) -> str:
    """The run's context part: the current time, then each non-empty section.

    Says what it is, because on every step after the first it sits after the
    newest tool result rather than before the person's message, and a model
    that read it as something the person typed would answer it.
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lines = [
        RUN_CONTEXT_OPEN,
        (
            "Lemma's standing context for this run, as of when it started. "
            "Nobody wrote this to you: it is background, and the person's own "
            "messages are what you are answering."
        ),
        "",
        f"Current date and time: {current.strftime('%Y-%m-%dT%H:%M:%SZ')} (UTC).",
    ]
    for section in sections:
        if section and section.strip():
            lines.extend(["", section.strip()])
    lines.append(RUN_CONTEXT_CLOSE)
    return "\n".join(lines)


def run_context_for(ctx: object, *, now: datetime | None = None) -> str:
    """The context the run-context builder rendered, or just the clock.

    Every run is told the time, including one whose context was assembled by
    something other than the run-context builder.
    """
    rendered = getattr(ctx, "run_context", None)
    if isinstance(rendered, str) and rendered.strip():
        return rendered
    return render_run_context(sections=(), now=now)


def stored_todos(conversation: Conversation) -> list[tuple[str, bool]]:
    """The conversation's task list as ``(content, done)``, oldest first."""
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    raw = metadata.get("todos")
    if not isinstance(raw, list):
        return []
    items: list[tuple[str, bool]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if content:
            # `status == "completed"` is the pre-simplification shape; rows in
            # that form are still in metadata on older conversations.
            items.append(
                (content, bool(entry.get("done")) or entry.get("status") == "completed")
            )
    return items


#: Not "# Task list": that is the heading of the todo tool's own guidance, and
#: two sections with one title read as one section said twice.
TASK_LIST_HEADING = "# Your current task list"


def task_list_section(
    conversation: Conversation, *, toolsets: Collection[AgentToolset]
) -> str:
    """Show the run its own task list, and say what finishing an item requires.

    Without this a run starts blind: the list lives in conversation metadata,
    and the tool return that last showed it is an old message that history
    trimming can drop. An agent that cannot see its own plan cannot tick
    anything off it.

    Empty for an agent without the todo toolset, and for a conversation that has
    never planned anything: an agent with no list should decide whether the work
    needs one, not be nagged about a checklist that does not exist.
    """
    if AgentToolset.TODO not in toolsets:
        return ""
    items = stored_todos(conversation)
    if not items:
        return ""
    rendered = "\n".join(
        f"- [{'x' if done else ' '}] {content}" for content, done in items
    )
    done_count = sum(1 for _, done in items if done)
    if done_count == len(items):
        return (
            f"{TASK_LIST_HEADING}\n"
            "Every item on this conversation's list is finished:\n\n"
            f"{rendered}\n\n"
            "That plan is history. If this message needs multi-step work, call "
            "`write_todos` with the new plan and it replaces the old one."
        )
    first_open = next(content for content, done in items if not done)
    return (
        f"{TASK_LIST_HEADING}\n"
        "This conversation already has a task list. Lemma stores it, the person "
        f"can see it, and right now it reads ({done_count} of {len(items)} "
        "done):\n\n"
        f"{rendered}\n\n"
        f"Pick up at the first unchecked item — **{first_open}** — unless this "
        "message sends you somewhere else. Check each item off with "
        f'`write_todos` (`["- [x] {first_open}"]`) as soon as it is finished.'
    )


MACHINE_HEADING = "## Machine"


def machine_section(
    *,
    ref: str,
    size: str,
    docker: bool,
    workdir: str | None,
    disk_fraction: float | None,
    compute_left: float | None,
    compute_resets: str | None,
) -> str:
    """Where this run's commands go, and how much room they have.

    Per run rather than in the stable prompt: the disk and the compute left
    change between runs, and a run that knows its machine is nearly full
    cleans up before a build fails halfway.
    """
    lines = [
        MACHINE_HEADING,
        f"- Machine: `{ref}`, {size} (vCPU x GB)" + (", with Docker" if docker else ""),
    ]
    if workdir:
        lines.append(
            f"- Shared with other runs: work in `{workdir}`, which is yours "
            "for this run. Processes you leave running stop when the run "
            "ends, unless started as a service."
        )
    if disk_fraction is not None:
        lines.append(
            f"- Disk: {disk_fraction:.0%} used"
            + (
                " -- free space with `reset_machine` before a big build."
                if disk_fraction >= 0.7
                else ""
            )
        )
    if compute_left is not None:
        lines.append(
            f"- Compute left this month: {compute_left:.0%}"
            + (f" (resets {compute_resets})" if compute_resets else "")
        )
    return "\n".join(lines)
