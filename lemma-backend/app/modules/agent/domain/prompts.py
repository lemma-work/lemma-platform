"""Base prompt composition for agent harnesses.

Every agent's system prompt is composed the same way: the pod resource map
(identical for every run, so it caches), then a base prompt (the
pod's own teammate, or a named agent), then a per-toolset guidance fragment for
each toolset the agent actually has, then the agent/conversation instructions and
the runtime context brief. Tool guidance lives once, in the fragment files mapped
by ``FRAGMENT_BY_TOOLSET`` — the teammate is rich because it has every toolset,
not because its base prompt restates each tool.

Resource authoring details live in skills; tool schemas describe arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.modules.agent.domain.agent_memory_paths import memory_is_active
from app.modules.agent.domain.prompt_directories import _directory_sections
from app.modules.agent.domain.value_objects import AgentToolset

if TYPE_CHECKING:
    from app.modules.agent.domain.context import AgentContext
    from app.modules.agent.domain.entities import Agent, Conversation

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_THE_POD_PROMPT_PATH = _PROMPT_DIR / "the_pod.md"
_TEAMMATE_PROMPT_PATH = _PROMPT_DIR / "teammate.md"
_AGENT_BASE_PROMPT_PATH = _PROMPT_DIR / "agent_base.md"
_CONNECTORS_PROMPT_PATH = _PROMPT_DIR / "connectors.md"
_REPLIES_PROMPT_PATH = _PROMPT_DIR / "replies.md"
_WORKSPACE_CLI_PROMPT_PATH = _PROMPT_DIR / "workspace_cli.md"
_SKILLS_PROMPT_PATH = _PROMPT_DIR / "skills.md"
_WEB_SEARCH_PROMPT_PATH = _PROMPT_DIR / "web_search.md"
_TODO_PROMPT_PATH = _PROMPT_DIR / "todo.md"
_MEMORY_PROMPT_PATH = _PROMPT_DIR / "memory.md"
_SPEECH_PROMPT_PATH = _PROMPT_DIR / "speech.md"
_MESSAGING_PROMPT_PATH = _PROMPT_DIR / "messaging.md"
_USER_INTERACTION_PROMPT_PATH = _PROMPT_DIR / "user_interaction.md"
_AGENT_HOST_RUNTIME_PROMPT_PATH = _PROMPT_DIR / "agent_host_runtime.md"

# Per-toolset prompt fragments, in the order they should appear in the system
# prompt. Only the visible/core toolsets that carry usage guidance are listed;
# deferred toolsets (pod/subagents) are surfaced via the deferred-tools hint
# instead. The pod-default assistant has all of these, so it gets them all.
# NB: in-process runs get these fragments through the matching pydantic-ai
# capabilities (build_agent_instructions is called with include_toolset_prompts=
# False); this map is the remote-harness path, which has no capability layer.
FRAGMENT_BY_TOOLSET: dict[AgentToolset, Path] = {
    AgentToolset.WORKSPACE_CLI: _WORKSPACE_CLI_PROMPT_PATH,
    AgentToolset.SKILLS: _SKILLS_PROMPT_PATH,
    AgentToolset.WEB_SEARCH: _WEB_SEARCH_PROMPT_PATH,
    AgentToolset.SPEECH: _SPEECH_PROMPT_PATH,
    AgentToolset.TODO: _TODO_PROMPT_PATH,
    AgentToolset.MEMORY: _MEMORY_PROMPT_PATH,
    AgentToolset.MESSAGING: _MESSAGING_PROMPT_PATH,
    # `display_resource` had no fragment on either path for a long time, on the
    # theory that the tool's own description was enough. It is enough for an
    # in-process run, where that description is a first-class tool definition
    # and nothing competes with it. It is not enough for a coding agent driven
    # through Agent Host, which meets the same text as one MCP tool among its
    # own file and shell tools, underneath its own system prompt telling it to
    # behave like a coding agent — so it answered in prose and never showed
    # anything. Convention belongs in the instructions, not only in a schema.
    AgentToolset.USER_INTERACTION: _USER_INTERACTION_PROMPT_PATH,
    # Connectors had no fragment at all, on either path. The toolset is deferred
    # behind ToolSearch, so an agent with connected accounts met a one-line
    # group label from the deferred-tools hint and nothing about the search →
    # describe → run loop, or about the fact that a connector operation is the
    # one call in the pod that acts on the world outside it.
    AgentToolset.CONNECTORS: _CONNECTORS_PROMPT_PATH,
}


def _read_required_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required agent prompt file is missing: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_the_pod_prompt() -> str:
    """Resource map shared by both agent kinds and harness paths."""
    return _read_required_prompt(_THE_POD_PROMPT_PATH)


def load_teammate_base_prompt() -> str:
    return _read_required_prompt(_TEAMMATE_PROMPT_PATH)


def load_agent_base_prompt() -> str:
    return _read_required_prompt(_AGENT_BASE_PROMPT_PATH)


def load_connectors_prompt() -> str:
    return _read_required_prompt(_CONNECTORS_PROMPT_PATH)


def load_replies_prompt() -> str:
    """What an assistant message is, how long it may be, and when the right
    answer is a reply rather than a run.

    Not keyed to a toolset: every agent replies, whatever tools it has, and the
    reply is the one thing the person always sees. Gating it on a toolset is how
    the narration in the Lemma UI went unruled for so long -- the equivalent
    rules existed only in the per-platform surface fragment, so a run with no
    surface platform (the web UI) was told nothing about length or narration.

    The trailing "not every message is a task" section is here for the same
    reason. "When you know enough to act, act" is the loudest line in either
    base prompt, and applied to "hi" it yields an interrogation or a tool
    spree -- so the rule that some messages want a sentence back has to sit
    with the reply rules, on the one path every agent takes.
    """
    return _read_required_prompt(_REPLIES_PROMPT_PATH)


def load_workspace_cli_prompt() -> str:
    return _read_required_prompt(_WORKSPACE_CLI_PROMPT_PATH)


def load_skills_prompt() -> str:
    return _read_required_prompt(_SKILLS_PROMPT_PATH)


def load_web_search_prompt() -> str:
    return _read_required_prompt(_WEB_SEARCH_PROMPT_PATH)


def load_todo_prompt() -> str:
    return _read_required_prompt(_TODO_PROMPT_PATH)


def load_messaging_prompt() -> str:
    return _read_required_prompt(_MESSAGING_PROMPT_PATH)


def load_speech_prompt() -> str:
    return _read_required_prompt(_SPEECH_PROMPT_PATH)


def load_user_interaction_prompt() -> str:
    return _read_required_prompt(_USER_INTERACTION_PROMPT_PATH)


def load_memory_prompt() -> str:
    """The memory contract: where durable facts live and how AGENTS.md is used.

    One file, read by both harnesses -- the remote one through
    ``FRAGMENT_BY_TOOLSET``, the in-process one through ``MemoryCapability``.
    It used to be three hand-synced copies (this fragment inside
    ``workspace_cli.md``, plus paragraphs in the ``pod_write_file`` and
    ``pod_read_file`` docstrings), which is two more than can stay true.
    """
    return _read_required_prompt(_MEMORY_PROMPT_PATH)


def load_agent_host_runtime_prompt() -> str:
    """Runtime guidance for a run driven through Agent Host (remote harness)."""
    return _read_required_prompt(_AGENT_HOST_RUNTIME_PROMPT_PATH)


def load_toolset_fragment(toolset: AgentToolset) -> str | None:
    """Return the guidance fragment for a toolset, or ``None`` if it has none."""
    path = FRAGMENT_BY_TOOLSET.get(toolset)
    return _read_required_prompt(path) if path is not None else None


def build_agent_instructions(
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: AgentContext,
    include_toolset_prompts: bool = True,
    runs_as_remote_process: bool = False,
) -> str:
    """Compose the full system prompt for an agent run.

    Layering: base prompt (pod-default vs user-agent) → reply discipline →
    per-toolset fragments → agent instruction → conversation instructions →
    runtime context brief.

    ``include_toolset_prompts`` controls whether the per-toolset fragments are
    folded in here. The in-process LEMMA harness passes ``False`` because those
    fragments are contributed by the matching pydantic-ai capabilities instead;
    remote harnesses keep ``True`` since they have no capability layer.

    ``runs_as_remote_process`` says this run is a coding agent executing as a
    real OS process on somebody's own computer, rather than inside the
    workspace sandbox. It changes what the working-directory section has to
    say, because such an agent has *two* directories and `pwd` answers with the
    wrong one.
    """

    # Shared resource guidance stays ahead of agent-specific content for caching.
    sections = [load_the_pod_prompt()]

    if conversation.is_pod_assistant:
        sections.append(load_teammate_base_prompt())
    else:
        sections.append(load_agent_base_prompt())

    # Unconditional, on both harness paths: reply discipline is not a toolset.
    # A surface run narrows this further -- ``platform_agent_guidance`` appends
    # its own ``soft_char_limit`` below -- but a run with no surface platform
    # would otherwise be told nothing at all about length or narration.
    sections.append(load_replies_prompt())

    enabled = _fragment_toolsets(agent=agent, conversation=conversation)

    if include_toolset_prompts:
        for toolset, path in FRAGMENT_BY_TOOLSET.items():
            if toolset in enabled:
                sections.append(_read_required_prompt(path))

        # Per-platform surface guidance for remote harnesses (which have no
        # capability layer). The in-process LEMMA harness passes
        # include_toolset_prompts=False and gets this from SurfacePlatformCapability
        # instead, so this never double-injects. Imported where it is used so the
        # prompt layer, which every run loads, does not carry the platform tables
        # a surface run needs.
        surface_platform = getattr(ctx, "surface_platform", None)
        if surface_platform:
            from app.modules.agent_surfaces.contracts.platforms import (
                platform_agent_guidance,
            )

            fragment = platform_agent_guidance(surface_platform)
            if fragment:
                sections.append(fragment)

    # The agent's actual working directory is dynamic (per conversation), so it
    # can't live in a static fragment. Inject it here so BOTH harnesses (in-process
    # passes include_toolset_prompts=False; remote passes True) and BOTH agent types
    # (pod-default + user) get told their cwd whenever they can run workspace tools.
    #
    # Native agents also have a host cwd, resolved by Agent Host at dispatch.
    # Keep the sandbox path scoped to its tools so neither path masquerades as
    # a mount that does not exist.
    sections.extend(
        _directory_sections(
            ctx=ctx,
            conversation=conversation,
            enabled=enabled,
            runs_as_remote_process=runs_as_remote_process,
        )
    )

    if agent.instruction.strip():
        sections.append("# Agent Instructions\n" + agent.instruction.strip())
    if conversation.instructions and conversation.instructions.strip():
        sections.append(
            "# Conversation Instructions\n" + conversation.instructions.strip()
        )
    # Runtime context (pod, user, granted resources) built once per run and
    # carried on the context.
    context_brief = getattr(ctx, "context_brief", None)
    if isinstance(context_brief, str) and context_brief.strip():
        sections.append(context_brief.strip())

    # The task list the conversation already has, if any. Without this a run
    # starts blind: the list lives in conversation metadata, and the tool return
    # that last showed it is an old message that history trimming can drop. An
    # agent that cannot see its own plan cannot tick anything off it, which is
    # exactly how a checklist written in turn one stays unchecked forever.
    #
    # Last on purpose. This is the most volatile thing in the prompt -- every
    # `write_todos` rewrites it -- and an OpenAI-compatible provider caches on
    # the literal prefix, so whatever sits after the first changed byte is what
    # gets re-read. Anything ahead of this stays cached across a checklist
    # update; anything behind it would not. Appended unconditionally; the join
    # below drops it when it is empty.
    sections.append(_task_list_section(conversation, enabled=enabled))
    return "\n\n---\n\n".join(
        section.strip() for section in sections if section.strip()
    )


def _stored_todos(conversation: Conversation) -> list[tuple[str, bool]]:
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


def _task_list_section(
    conversation: Conversation, *, enabled: set[AgentToolset]
) -> str:
    """Show the run its own task list, and say what finishing an item requires.

    Empty for an agent without the todo toolset, and for a conversation that has
    never planned anything: an agent with no list should decide whether the work
    needs one, not be nagged about a checklist that does not exist.
    """
    if AgentToolset.TODO not in enabled:
        return ""
    items = _stored_todos(conversation)
    if not items:
        return ""
    rendered = "\n".join(
        f"- [{'x' if done else ' '}] {content}" for content, done in items
    )
    done_count = sum(1 for _, done in items if done)
    if done_count == len(items):
        return (
            "# Task list\n"
            "Every item on this conversation's list is finished:\n\n"
            f"{rendered}\n\n"
            "That plan is history. If this message needs multi-step work, call "
            "`write_todos` with the new plan and it replaces the old one."
        )
    first_open = next(content for content, done in items if not done)
    return (
        "# Task list\n"
        "This conversation already has a task list. Lemma stores it, the person "
        f"can see it, and right now it reads ({done_count} of {len(items)} "
        "done):\n\n"
        f"{rendered}\n\n"
        f"Pick up at the first unchecked item — **{first_open}** — unless this "
        "message sends you somewhere else. Check each item off with "
        f'`write_todos` (`["- [x] {first_open}"]`) as you finish it, before '
        "starting the next."
    )


def _fragment_toolsets(
    *,
    agent: Agent,
    conversation: Conversation,
) -> set[AgentToolset]:
    """Toolsets whose guidance fragment should be included for this run."""
    if conversation.is_pod_assistant:
        # The pod-default assistant runs the full batteries-included toolset, so it
        # gets every fragment regardless of the (possibly synthetic) agent passed.
        return set(FRAGMENT_BY_TOOLSET)
    enabled: set[AgentToolset] = set()
    for name in agent.toolsets:
        try:
            enabled.add(AgentToolset(name))
        except ValueError:  # pragma: no cover - defensive
            continue
    # Memory is the one fragment that can be configured and still be useless:
    # it carries no tools, so without WORKSPACE_CLI or POD it would teach an
    # agent to write files it has no way to write. Same predicate the brief's
    # memory section and the in-process capability gate on.
    if AgentToolset.MEMORY in enabled and not memory_is_active(enabled):
        enabled.discard(AgentToolset.MEMORY)
    return enabled
