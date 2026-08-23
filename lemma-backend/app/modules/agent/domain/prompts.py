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

from app.modules.agent.domain.agent_memory_paths import memory_is_active
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

    Layering: base prompt (pod-default vs user-agent) → per-toolset fragments →
    agent instruction → conversation instructions → runtime context brief.

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

    if conversation.is_pod_assistant:
        sections = [load_pod_assistant_base_prompt()]
    else:
        sections = [load_agent_base_prompt()]

    enabled = _fragment_toolsets(agent=agent, conversation=conversation)

    if include_toolset_prompts:
        for toolset, path in FRAGMENT_BY_TOOLSET.items():
            if toolset in enabled:
                sections.append(_read_required_prompt(path))

        # Per-platform surface guidance for remote harnesses (which have no
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
    # passes include_toolset_prompts=False; remote passes True) and BOTH agent types
    # (pod-default + user) get told their cwd whenever they can run workspace tools.
    #
    # `runs_as_remote_process` widens that to every Agent Host run, whether or
    # not it has workspace tools. A remote harness is a coding agent running as
    # a real OS process on somebody's Mac, and its *own* cwd is a Lemma scratch
    # directory that has nothing to do with the workspace. Left unsaid, the
    # agent believes the empty directory it was started in is the workspace --
    # which is exactly what "we want to build this on lemma (but locally)"
    # walked into. The in-process harness has no such second directory, so it
    # only needs this when it can act on it.
    if AgentToolset.WORKSPACE_CLI in enabled or runs_as_remote_process:
        sections.append(
            _workspace_directory_section(
                ctx=ctx,
                conversation=conversation,
                has_workspace_tools=AgentToolset.WORKSPACE_CLI in enabled,
                runs_as_remote_process=runs_as_remote_process,
            )
        )

    # The task list the conversation already has, if any. Without this a run
    # starts blind: the list lives in conversation metadata, and the tool return
    # that last showed it is an old message that history trimming can drop. An
    # agent that cannot see its own plan cannot tick anything off it, which is
    # exactly how a checklist written in turn one stays unchecked forever.
    # Appended unconditionally; the join below drops it when it is empty.
    sections.append(_task_list_section(conversation, enabled=enabled))

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
        # Not guarded. The only implementation reads a field and formats a
        # string, and swallowing a failure here would put a directory in the
        # prompt that the tools do not use -- which is the precise bug this
        # resolution exists to prevent, made silent.
        value = get_cwd()
        if value:
            return str(value)
    return f"/workspace/conversations/{conversation.id}"


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
        "message sends you somewhere else. The moment you finish an item, call "
        "`write_todos` with that one line checked "
        f'(`["- [x] {first_open}"]`) and only then start the next. A finished '
        "item still showing unchecked is not a cosmetic problem: it is what the "
        "person is reading to know where you are."
    )


def _workspace_repo(ctx: AgentContext):
    """The repository this conversation works in, if it was started on one."""
    return getattr(ctx, "workspace_repo", None)


def _project_paragraph(repo) -> str:
    """What an agent needs to know when its cwd is a real checkout."""
    on_ref = f" on `{repo.ref}`" if repo.ref else ""
    return (
        f"This directory is a git checkout of **{repo.full_name}**{on_ref}, "
        "cloned for you before this command ran. `git` and `gh` are already "
        "authenticated as the connected account, and a commit identity is "
        "already set — don't configure either.\n\n"
        "The checkout is shared, not yours alone: another conversation may be "
        "working in it right now. Run `git status` before you assume the tree "
        "is clean, and never `reset --hard`, `clean`, or force-switch a branch "
        "to tidy up — work on a branch of your own instead. An empty directory "
        "here means the clone failed, and a notice will have said so."
    )


def _workspace_directory_section(
    *,
    ctx: AgentContext,
    conversation: Conversation,
    has_workspace_tools: bool = True,
    runs_as_remote_process: bool = False,
) -> str:
    cwd = _workspace_cwd(ctx, conversation)
    if not has_workspace_tools:
        # A remote harness with no workspace toolset. It still has a local
        # process directory that looks exactly like a working directory, so the
        # only useful thing to say is that it is not one.
        return (
            "# Working Directory\n"
            "The directory this process started in is scratch space belonging "
            "to Lemma, not a workspace. It is not backed up, nobody else can "
            "see it, and it is swept once this conversation goes quiet.\n\n"
            "You have no workspace tools on this run, so there is nowhere to "
            "run commands or keep files. Do the work in your reply. If the task "
            "genuinely needs a shell or a filesystem, say so rather than using "
            "the machine you are running on."
        )
    repo = _workspace_repo(ctx)
    orientation = (
        _project_paragraph(repo)
        if repo is not None
        else (
            "An empty working directory means this is a **new conversation**, "
            "not a reset sandbox. Earlier conversations' work is still on disk "
            "under another `/workspace/c/<date>/<slug>`; list `/workspace/c/` "
            "to find it. Treat prior files as gone only if a tool result says "
            "the workspace was recreated."
        )
    )
    # For a remote harness the sentence "your working directory is X" is not
    # only informative, it is a correction: the agent is a real OS process and
    # `pwd` will answer with something else entirely. Naming both, and saying
    # which one is real, is the whole point.
    where = (
        (
            f"Your working directory is `{cwd}`, and you reach it **only "
            "through the Lemma tools** — `exec_command`, `execute_python` and "
            "the file tools. That is the workspace: a sandbox Lemma runs for "
            "this conversation.\n\n"
            "It is **not** the directory this process started in. `pwd` will "
            "answer with a Lemma scratch directory on somebody's own computer: "
            "it is swept, nobody can see it, and nothing you leave there is "
            "part of the conversation. Do not read or write anywhere else on "
            "that machine either — not the home directory, not a project "
            "folder, not even one the user names. If they ask for work on a "
            "local folder, say you work in the Lemma workspace and offer to do "
            "it there."
        )
        if runs_as_remote_process
        else (
            f"Your working directory is `{cwd}`. Files you write here are "
            "private to you until you upload them to pod files."
        )
    )
    return (
        "# Working Directory\n"
        f"{where}\n\n"
        f"{orientation}\n\n"
        "Files under `/workspace` survive an idle pause; running processes and "
        "your `execute_python` kernel do not, so don't plan around a background "
        "process living between turns. A `cd` in one `exec_command` does not "
        "carry to the next — pass `workdir` or use relative paths."
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
