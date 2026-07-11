"""Base prompt composition for agent harnesses.

Every agent's system prompt is composed the same way: a base prompt (rich for the
pod-default assistant, lean for user-created agents) plus a per-toolset guidance
fragment for each toolset the agent actually has, then the agent/conversation
instructions and the runtime context brief. Tool guidance lives once, in the
fragment files mapped by ``FRAGMENT_BY_TOOLSET`` — the pod-default assistant is
rich because it has every toolset, not because its base prompt restates each tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.modules.agent.domain.value_objects import AgentToolset

if TYPE_CHECKING:
    from app.modules.agent.domain.context import AgentContext
    from app.modules.agent.domain.entities import Agent, Conversation

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_POD_ASSISTANT_PROMPT_PATH = _PROMPT_DIR / "pod_assistant.md"
_AGENT_BASE_PROMPT_PATH = _PROMPT_DIR / "agent_base.md"
_WORKSPACE_CLI_PROMPT_PATH = _PROMPT_DIR / "workspace_cli.md"
_SKILLS_PROMPT_PATH = _PROMPT_DIR / "skills.md"
_WEB_SEARCH_PROMPT_PATH = _PROMPT_DIR / "web_search.md"
_TODO_PROMPT_PATH = _PROMPT_DIR / "todo.md"
_SPEECH_PROMPT_PATH = _PROMPT_DIR / "speech.md"

# Per-toolset prompt fragments, in the order they should appear in the system
# prompt. Only the visible/core toolsets that carry usage guidance are listed;
# deferred toolsets (pod/subagents) are surfaced via the deferred-tools hint
# instead. The pod-default assistant has all of these, so it gets them all.
# NB: in-process runs get these fragments through the matching pydantic-ai
# capabilities (build_agent_instructions is called with include_toolset_prompts=
# False); this map is the daemon-harness path, which has no capability layer.
FRAGMENT_BY_TOOLSET: dict[AgentToolset, Path] = {
    AgentToolset.WORKSPACE_CLI: _WORKSPACE_CLI_PROMPT_PATH,
    AgentToolset.SKILLS: _SKILLS_PROMPT_PATH,
    AgentToolset.WEB_SEARCH: _WEB_SEARCH_PROMPT_PATH,
    AgentToolset.SPEECH: _SPEECH_PROMPT_PATH,
    AgentToolset.TODO: _TODO_PROMPT_PATH,
}


def _read_required_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required agent prompt file is missing: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_pod_assistant_base_prompt() -> str:
    return _read_required_prompt(_POD_ASSISTANT_PROMPT_PATH)


def load_agent_base_prompt() -> str:
    return _read_required_prompt(_AGENT_BASE_PROMPT_PATH)


def load_workspace_cli_prompt() -> str:
    return _read_required_prompt(_WORKSPACE_CLI_PROMPT_PATH)


def load_skills_prompt() -> str:
    return _read_required_prompt(_SKILLS_PROMPT_PATH)


def load_web_search_prompt() -> str:
    return _read_required_prompt(_WEB_SEARCH_PROMPT_PATH)


def load_todo_prompt() -> str:
    return _read_required_prompt(_TODO_PROMPT_PATH)


def load_speech_prompt() -> str:
    return _read_required_prompt(_SPEECH_PROMPT_PATH)


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
) -> str:
    """Compose the full system prompt for an agent run.

    Layering: base prompt (pod-default vs user-agent) → per-toolset fragments →
    agent instruction → conversation instructions → runtime context brief.

    ``include_toolset_prompts`` controls whether the per-toolset fragments are
    folded in here. The in-process LEMMA harness passes ``False`` because those
    fragments are contributed by the matching pydantic-ai capabilities instead;
    daemon harnesses keep ``True`` since they have no capability layer.
    """

    if conversation.is_pod_assistant:
        sections = [load_pod_assistant_base_prompt()]
    else:
        sections = [load_agent_base_prompt()]

    enabled = _fragment_toolsets(agent=agent, conversation=conversation)

    if include_toolset_prompts:
        for toolset, path in FRAGMENT_BY_TOOLSET.items():
            if toolset in enabled:
                sections.append(_read_required_prompt(path))

        # Per-platform surface guidance for daemon harnesses (which have no
        # capability layer). The in-process LEMMA harness passes
        # include_toolset_prompts=False and gets this from SurfacePlatformCapability
        # instead, so this never double-injects. Lazy import avoids an
        # agent -> agent_surfaces module-load cycle.
        surface_platform = getattr(ctx, "surface_platform", None)
        if surface_platform:
            from app.composition.agent_surface_runtime import platform_agent_guidance

            fragment = platform_agent_guidance(surface_platform)
            if fragment:
                sections.append(fragment)

    # The agent's actual working directory is dynamic (per conversation), so it
    # can't live in a static fragment. Inject it here so BOTH harnesses (in-process
    # passes include_toolset_prompts=False; daemon passes True) and BOTH agent types
    # (pod-default + user) get told their cwd whenever they can run workspace tools.
    if AgentToolset.WORKSPACE_CLI in enabled:
        sections.append(_workspace_directory_section(ctx=ctx, conversation=conversation))

    if agent.instruction.strip():
        sections.append("# Agent Instructions\n" + agent.instruction.strip())
    if conversation.instructions and conversation.instructions.strip():
        sections.append(
            "# Conversation Instructions\n" + conversation.instructions.strip()
        )
    # Runtime context (pod, user, granted resources) built once per run and
    # carried on the context; always appended last so it grounds the agent.
    context_brief = getattr(ctx, "context_brief", None)
    if isinstance(context_brief, str) and context_brief.strip():
        sections.append(context_brief.strip())
    return "\n\n---\n\n".join(
        section.strip() for section in sections if section.strip()
    )


def _workspace_cwd(ctx: AgentContext, conversation: Conversation) -> str:
    """Resolve the agent's workspace working directory for the prompt.

    Prefers the resolved ``workspace_cwd`` carried on the run context (set from
    conversation metadata or the default by ``resolve_workspace_location``), then
    ``get_workspace_cwd()`` if present, then the conversation-scoped default.
    """
    cwd = getattr(ctx, "workspace_cwd", None)
    if cwd:
        return str(cwd)
    get_cwd = getattr(ctx, "get_workspace_cwd", None)
    if callable(get_cwd):
        try:
            value = get_cwd()
        except Exception:  # pragma: no cover - defensive
            value = None
        if value:
            return str(value)
    return f"/workspace/conversations/{conversation.id}"


def _workspace_directory_section(
    *,
    ctx: AgentContext,
    conversation: Conversation,
) -> str:
    cwd = _workspace_cwd(ctx, conversation)
    return (
        "# Working Directory\n"
        f"Your working directory in the workspace sandbox is `{cwd}`. The whole "
        "sandbox is yours: do all of your work here by default and create "
        "subfolders under it as you need them. Files you write here persist for "
        "this conversation and are private to you — the user does not see them. "
        "Do NOT work in `/tmp` or other locations; they are scratch space that "
        "gets wiped.\n\n"
        "Your `execute_python` kernel and `exec_command` shell both run in this "
        "directory, so write to relative paths (e.g. `chart.png`, "
        "`data/out.csv`) to keep files here — don't hardcode `/tmp/...`. To use a "
        "Python package that isn't installed yet, run `pip install <package>` (via "
        "`exec_command`), then import it in `execute_python` — they share the same "
        "interpreter. Use plain `pip install` (not `uv pip install`).\n\n"
        "When you produce a deliverable for the user, save it into the pod file "
        "system with the `lemma` CLI — default to the user's personal files at "
        "`/me/<topic-folder>/...` (e.g. `lemma files upload ./report.md "
        "/me/reports/report.md`) — and share that pod path, not the private "
        "sandbox path."
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
    return enabled
