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

from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.domain.agent_memory_paths import memory_is_active
from app.modules.agent.domain.prompt.directories import _directory_sections
from app.modules.agent.domain.value_objects import AgentToolset

if TYPE_CHECKING:
    from app.modules.agent.domain.context import ToolContext
    from app.modules.agent.domain.entities import Agent, Conversation

_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
_THE_POD_PROMPT_PATH = _PROMPT_DIR / "the_pod.md"
_TEAMMATE_PROMPT_PATH = _PROMPT_DIR / "teammate.md"
_AGENT_BASE_PROMPT_PATH = _PROMPT_DIR / "agent_base.md"
_CONNECTORS_PROMPT_PATH = _PROMPT_DIR / "connectors.md"
_REPLIES_PROMPT_PATH = _PROMPT_DIR / "replies.md"
_WORKSPACE_CLI_PROMPT_PATH = _PROMPT_DIR / "workspace_cli.md"
_WORKSPACE_CLI_HOST_EXECUTION_PROMPT_PATH = (
    _PROMPT_DIR / "workspace_cli_host_execution.md"
)
_POD_PROMPT_PATH = _PROMPT_DIR / "pod.md"
_SKILLS_PROMPT_PATH = _PROMPT_DIR / "skills.md"
_WEB_SEARCH_PROMPT_PATH = _PROMPT_DIR / "web_search.md"
_TODO_PROMPT_PATH = _PROMPT_DIR / "todo.md"
_MEMORY_PROMPT_PATH = _PROMPT_DIR / "memory.md"
_MEMORY_AFTER_TURN_PROMPT_PATH = _PROMPT_DIR / "memory_after_turn.md"
_MEMORY_PASS_PROMPT_PATH = _PROMPT_DIR / "memory_pass.md"
#: memory.md's sections, and the one that differs between a run that saves
#: memory itself and one whose memory is kept after it ends.
_MEMORY_SECTION_HEADING = "### "
_MEMORY_TIMING_SECTION = "When to write"
_SPEECH_PROMPT_PATH = _PROMPT_DIR / "speech.md"
_MESSAGING_PROMPT_PATH = _PROMPT_DIR / "messaging.md"
_SUBAGENTS_PROMPT_PATH = _PROMPT_DIR / "subagents.md"
_USER_INTERACTION_PROMPT_PATH = _PROMPT_DIR / "user_interaction.md"
_AGENT_HOST_RUNTIME_PROMPT_PATH = _PROMPT_DIR / "agent_host_runtime.md"
_AGENT_HOST_HOST_EXECUTION_PROMPT_PATH = _PROMPT_DIR / "agent_host_host_execution.md"

# Per-toolset prompt fragments, in the order they should appear in the system
# prompt. A toolset is listed here if it carries usage guidance, whether or not
# it ends up deferred: deferral hides a toolset's schemas, not its contract.
# The pod-default assistant has all of these, so it gets them all.
# NB: in-process runs get these fragments through the matching pydantic-ai
# capabilities (build_agent_instructions is called with include_toolset_prompts=
# False); this map is the remote-harness path, which has no capability layer.
FRAGMENT_BY_TOOLSET: dict[AgentToolset, Path] = {
    # Before WORKSPACE_CLI on purpose. The CLI fragment is a worked cookbook and
    # the pod tools are deferred behind a search, so an agent that met the CLI
    # first reached for `lemma query run` in `exec_command` and never went
    # looking -- traces showed whole runs doing every table and file read that
    # way. Stating the default before the exception is the cheap half of the
    # fix; the other half was deleting the duplicate recipes from the cookbook.
    AgentToolset.POD: _POD_PROMPT_PATH,
    AgentToolset.WORKSPACE_CLI: _WORKSPACE_CLI_PROMPT_PATH,
    AgentToolset.SKILLS: _SKILLS_PROMPT_PATH,
    AgentToolset.WEB_SEARCH: _WEB_SEARCH_PROMPT_PATH,
    AgentToolset.SPEECH: _SPEECH_PROMPT_PATH,
    AgentToolset.TODO: _TODO_PROMPT_PATH,
    AgentToolset.MEMORY: _MEMORY_PROMPT_PATH,
    AgentToolset.MESSAGING: _MESSAGING_PROMPT_PATH,
    # A coding agent on Agent Host met `lemma_spawn_subagent` as one MCP tool
    # beside its own Task/Agent tool and never used it: nothing said to, and
    # its own system prompt says delegate with the native one, whose work Lemma
    # never sees. The spawn -> wait_for -> end-the-turn loop is not in any one
    # tool's description either.
    AgentToolset.SUBAGENTS: _SUBAGENTS_PROMPT_PATH,
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


def load_workspace_cli_prompt(*, host_execution: bool = False) -> str:
    """The workspace tools' contract, for the machine the commands run on.

    With ``host_execution`` the sections that describe the VM -- a persistent
    home, `/tmp`, the preinstalled libraries and `lit` -- are replaced by the
    ones in ``workspace_cli_host_execution.md``, which describe the user's Mac
    under host execution (docs/architecture/desktop-host-execution.md §6).
    Sections the two share are kept once, so they cannot drift apart.
    """
    base = _read_required_prompt(_WORKSPACE_CLI_PROMPT_PATH)
    if not host_execution:
        return base
    replacements = _prompt_sections(
        _read_required_prompt(_WORKSPACE_CLI_HOST_EXECUTION_PROMPT_PATH),
        heading="## ",
    )
    return "\n\n".join(
        replacements.get(title, body).strip()
        for title, body in _prompt_sections(base, heading="## ").items()
    )


def load_pod_prompt() -> str:
    """Which tool reaches pod tables and files, and when the CLI is still right.

    Deferred toolsets still carry their contract (see ``connectors``), and this
    one has to carry more than a contract: `exec_command` is visible while the
    `pod_*` tools are a search away, so the fragment exists to say which of two
    working paths is the default.
    """
    return _read_required_prompt(_POD_PROMPT_PATH)


def load_skills_prompt() -> str:
    return _read_required_prompt(_SKILLS_PROMPT_PATH)


def load_web_search_prompt() -> str:
    return _read_required_prompt(_WEB_SEARCH_PROMPT_PATH)


def load_todo_prompt() -> str:
    return _read_required_prompt(_TODO_PROMPT_PATH)


def load_messaging_prompt() -> str:
    return _read_required_prompt(_MESSAGING_PROMPT_PATH)


def load_subagents_prompt() -> str:
    return _read_required_prompt(_SUBAGENTS_PROMPT_PATH)


def load_speech_prompt() -> str:
    return _read_required_prompt(_SPEECH_PROMPT_PATH)


def load_user_interaction_prompt() -> str:
    return _read_required_prompt(_USER_INTERACTION_PROMPT_PATH)


def load_memory_prompt(*, after_turn: bool = False) -> str:
    """The memory contract: where durable facts live and how AGENTS.md is used.

    One file, read by both harnesses -- the remote one through
    ``FRAGMENT_BY_TOOLSET``, the in-process one through ``MemoryCapability``.
    It used to be three hand-synced copies (this fragment inside
    ``workspace_cli.md``, plus paragraphs in the ``pod_write_file`` and
    ``pod_read_file`` docstrings), which is two more than can stay true.

    With ``after_turn`` its ``When to write`` section is the one in
    ``memory_after_turn.md``: the run a person is waiting on, whose memory a
    pass keeps once it ends (``memory_kept_after_turn``). Only that section
    differs, so what is worth keeping and how it is written cannot drift
    between the two.
    """
    contract = _read_required_prompt(_MEMORY_PROMPT_PATH)
    if not after_turn:
        return contract
    return _replace_section(
        contract,
        _read_required_prompt(_MEMORY_AFTER_TURN_PROMPT_PATH),
        title=_MEMORY_TIMING_SECTION,
    )


def load_memory_pass_prompt() -> str:
    """What the memory pass is told: its own framing, then the contract.

    The contract without its ``When to write`` section, which speaks to an
    agent in the middle of a turn. The pass runs once the turn is over, and
    keeps memory by the same rules the agent itself writes it by.
    """
    sections = _prompt_sections(
        _read_required_prompt(_MEMORY_PROMPT_PATH), heading=_MEMORY_SECTION_HEADING
    )
    if sections.pop(_MEMORY_TIMING_SECTION, None) is None:
        raise ValueError(f"memory.md has no '{_MEMORY_TIMING_SECTION}' section")
    rules = "\n\n".join(body.strip() for body in sections.values())
    return f"{_read_required_prompt(_MEMORY_PASS_PROMPT_PATH)}\n\n{rules}"


def _replace_section(text: str, replacement: str, *, title: str) -> str:
    """``text`` with its memory section named ``title`` swapped for ``replacement``.

    Raises rather than returning ``text`` unchanged: a renamed heading would
    otherwise tell a run the rule it was meant not to get.
    """
    sections = _prompt_sections(text, heading=_MEMORY_SECTION_HEADING)
    if title not in sections:
        raise ValueError(f"no '{title}' section to replace")
    sections[title] = replacement
    return "\n\n".join(body.strip() for body in sections.values())


def load_agent_host_runtime_prompt(*, host_execution: bool = False) -> str:
    """Runtime guidance for a run driven through Agent Host (remote harness).

    With ``host_execution`` the ``# Runtime`` and ``# Browser`` sections are
    replaced by the ones in ``agent_host_host_execution.md``: that run has no
    Lemma command tools (docs/architecture/desktop-host-execution.md §7), so
    the sections telling it to use them would send it to tools that are not
    there. Every other section is shared, so the two cannot drift apart.
    """
    base = _read_required_prompt(_AGENT_HOST_RUNTIME_PROMPT_PATH)
    if not host_execution:
        return base
    replacements = _prompt_sections(
        _read_required_prompt(_AGENT_HOST_HOST_EXECUTION_PROMPT_PATH)
    )
    return "\n\n".join(
        replacements.get(title, body).strip()
        for title, body in _prompt_sections(base).items()
    )


def _prompt_sections(text: str, *, heading: str = "# ") -> dict[str, str]:
    """A Markdown prompt's sections at one heading level, by title, in order."""
    sections: dict[str, str] = {}
    title = ""
    for line in text.splitlines(keepends=True):
        if line.startswith(heading):
            title = line[len(heading) :].strip()
        sections[title] = sections.get(title, "") + line
    return sections


def load_toolset_fragment(toolset: AgentToolset) -> str | None:
    """Return the guidance fragment for a toolset, or ``None`` if it has none."""
    path = FRAGMENT_BY_TOOLSET.get(toolset)
    return _read_required_prompt(path) if path is not None else None


def build_agent_instructions(
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: ToolContext,
    include_toolset_prompts: bool = True,
    runs_as_remote_process: bool = False,
) -> str:
    """The whole system prompt as one string; see `build_agent_instruction_parts`."""
    stable, per_conversation = build_agent_instruction_parts(
        agent=agent,
        conversation=conversation,
        ctx=ctx,
        include_toolset_prompts=include_toolset_prompts,
        runs_as_remote_process=runs_as_remote_process,
    )
    return _SEPARATOR.join(part for part in (stable, per_conversation) if part)


_SEPARATOR = "\n\n---\n\n"


def build_agent_instruction_parts(
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: ToolContext,
    include_toolset_prompts: bool = True,
    runs_as_remote_process: bool = False,
) -> tuple[str, str]:
    """Compose the system prompt for an agent run.

    Returned in two parts: what is the same for every conversation with this
    agent (base prompt, reply discipline, per-toolset fragments, the agent's
    own instruction), and what belongs to this conversation (working directory,
    conversation instructions, the runtime brief). A provider caches the literal
    prefix, so the split is the cache boundary: the in-process harness puts the
    capability guidance between the two, which keeps that large and unchanging
    block inside the prefix a new conversation can reuse, rather than behind a
    working directory that differs in every conversation.

    Nothing here may change between two runs of one conversation: the replayed
    history sits behind it, and a changed byte here re-reads all of it. What
    does change run to run -- the task list, the attached doc, memory, open
    requests -- is in `run_context_prompt` instead.

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
    # A surface run narrows this further -- ``surface_platform_guidance`` appends
    # its own ``soft_char_limit`` below -- but a run with no surface platform
    # would otherwise be told nothing at all about length or narration.
    sections.append(load_replies_prompt())

    enabled = _fragment_toolsets(agent=agent, conversation=conversation, ctx=ctx)

    if include_toolset_prompts:
        sections.extend(_remote_fragments(enabled, ctx=ctx))

    if agent.instruction.strip():
        sections.append("# Agent Instructions\n" + agent.instruction.strip())

    stable = sections
    # The agent's working directory is per conversation, so it cannot live in a
    # static fragment. Both harnesses and both agent kinds are told their cwd
    # here whenever they can run workspace tools. A native agent also has a host
    # cwd, resolved by Agent Host at dispatch; the sandbox path stays scoped to
    # its tools so neither masquerades as a mount that does not exist.
    sections = list(
        _directory_sections(
            ctx=ctx,
            conversation=conversation,
            enabled=enabled,
            runs_as_remote_process=runs_as_remote_process,
        )
    )
    if conversation.instructions and conversation.instructions.strip():
        sections.append(
            "# Conversation Instructions\n" + conversation.instructions.strip()
        )
    # The part of the runtime brief that holds for the conversation: the pod,
    # the person, this agent, and what it can reach.
    context_brief = getattr(ctx, "context_brief", None)
    if isinstance(context_brief, str) and context_brief.strip():
        sections.append(context_brief.strip())
    return (
        _SEPARATOR.join(section.strip() for section in stable if section.strip()),
        _SEPARATOR.join(section.strip() for section in sections if section.strip()),
    )


def _remote_fragments(enabled: set[AgentToolset], *, ctx: ToolContext) -> list[str]:
    """The per-toolset guidance a harness with no capability layer is given.

    The in-process LEMMA harness gets the same text from its capabilities, and
    passes ``include_toolset_prompts=False`` so this never double-injects.
    """
    # An Agent Host run with host execution on has no Lemma command tools
    # (docs/architecture/desktop-host-execution.md §7): its own runtime section
    # says so, and the workspace contract -- a VM home, `exec_command` -- would
    # contradict it.
    native_commands = bool(getattr(ctx, "host_runs_native_commands", False))
    fragments = [
        _read_required_prompt(path)
        for toolset, path in FRAGMENT_BY_TOOLSET.items()
        if toolset in enabled
        and not (toolset is AgentToolset.WORKSPACE_CLI and native_commands)
    ]
    # Imported where it is used so the prompt layer, which every run loads,
    # does not carry the platform tables a surface run needs.
    surface_platform = getattr(ctx, "surface_platform", None)
    if surface_platform:
        from app.modules.agent.domain.prompt.surface import (
            is_group_conversation,
            surface_platform_guidance,
        )

        fragment = surface_platform_guidance(
            surface_platform,
            audience=run_audience(ctx),
            in_group=is_group_conversation(ctx),
        )
        if fragment:
            fragments.append(fragment)
    return fragments


def _fragment_toolsets(
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: ToolContext,
) -> set[AgentToolset]:
    """Toolsets whose guidance this run gets: the ones it actually has.

    The run's *effective* toolsets -- declared, always-on and grant-derived, as
    `RunToolAssembler` builds tools from -- not the agent's declared list. Read
    from the declared list, a named agent was never shown its stored task list
    (TODO is always-on, never declared), and one with a folder grant but no
    WORKSPACE_CLI was never told where its pod files are.
    """
    run_toolsets = getattr(ctx, "run_toolsets", None)
    if run_toolsets is None:
        # A context the run-context builder did not make; resolve the same way
        # it does, minus grants it cannot see.
        from app.modules.agent.infrastructure.tools.toolset_selection import (
            resolve_toolset_names,
        )

        run_toolsets, _ = resolve_toolset_names(
            None if conversation.is_pod_assistant else agent, conversation
        )
    enabled = set(run_toolsets)
    # Memory is the one fragment that can be present and still be useless: it
    # carries no tools, so without WORKSPACE_CLI or POD it would teach an agent
    # to write files it has no way to write. Same predicate the brief's memory
    # section and the in-process capability gate on.
    if AgentToolset.MEMORY in enabled and not memory_is_active(enabled):
        enabled.discard(AgentToolset.MEMORY)
    return enabled
