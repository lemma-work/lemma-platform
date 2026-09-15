"""Where an agent's two working directories are, and what it may assume there.

Split out of ``prompts`` because it is a self-contained section of the system
prompt with its own vocabulary -- a sandbox under ``/workspace``, a pod-files
directory under ``/me``, and for a coding agent on somebody's own machine a
third one that ``pwd`` answers with.

The pair is one decision, which is why it is one module: the workspace section
has to know whether there is a pod directory to point at, and an agent told
about neither goes looking for a person's attachment in a sandbox it was never
put in.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.services.workspace_location import (
    resolve_pod_cwd,
    resolve_workspace_location,
)

if TYPE_CHECKING:
    from app.modules.agent.domain.context import AgentContext
    from app.modules.agent.domain.entities import Conversation


# Path segments this file is willing to write into a Markdown code span.
_PLAIN_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._@+-]{1,64}")
# A whole path it is willing to write into one: absolute, plain segments only.
_PLAIN_PATH = re.compile(r"(?:/[A-Za-z0-9._@+-]{1,64})+/?")


def _workspace_cwd(ctx: AgentContext, conversation: Conversation) -> str:
    """Resolve the agent's workspace working directory for the prompt.

    Prefers the resolved ``workspace_cwd`` carried on the run context, then
    ``get_workspace_cwd()`` if present, then the conversation's own resolution.

    That last step delegates rather than formatting a path here. It used to
    return ``/workspace/conversations/{id}``, which is not a directory this
    platform has made for a long time -- the cwd is persisted in conversation
    metadata and falls back to ``/workspace/c/{date}/{slug}``. So whenever the
    context did not carry a cwd, the one section whose entire job is to say
    where the agent is named somewhere that does not exist.

    ``workspace_location`` says it in its own docstring: two implementations of
    this ladder put a person's attachment in a directory the agent's cwd never
    points at. There is one ladder, and this is a caller of it.
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
    return resolve_workspace_location(conversation).cwd


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


def _directory_sections(
    *,
    ctx: AgentContext,
    conversation: Conversation,
    enabled: set[AgentToolset],
    runs_as_remote_process: bool,
) -> list[str]:
    """Which of the two working directories this run is told about.

    The agent has up to two, and which ones it needs depends on what it can
    reach: a sandbox under ``/workspace`` if it can run commands there, and a
    pod-files directory under ``/me`` if it can reach pod files at all. Kept
    together because the pair is one decision -- the workspace section has to
    know whether there is a pod directory to point at, and an agent told about
    neither goes looking for a person's attachment in a sandbox it was never
    put in.
    """
    has_pod_files = AgentToolset.POD in enabled or AgentToolset.WORKSPACE_CLI in enabled
    sections: list[str] = []
    if AgentToolset.WORKSPACE_CLI in enabled or runs_as_remote_process:
        sections.append(
            _workspace_directory_section(
                ctx=ctx,
                conversation=conversation,
                has_workspace_tools=AgentToolset.WORKSPACE_CLI in enabled,
                runs_as_remote_process=runs_as_remote_process,
                has_pod_files=has_pod_files,
            )
        )
    if has_pod_files:
        sections.append(_pod_directory_section(ctx=ctx, conversation=conversation))
    return sections


def _pod_cwd(ctx: AgentContext, conversation: Conversation) -> str:
    """Where relative pod-file paths resolve, for the prompt.

    Same resolution order as ``_workspace_cwd`` and the same reason: the tools
    read this from the run context, so the prompt has to name what they use
    rather than a directory of its own.
    """
    cwd = getattr(ctx, "pod_cwd", None)
    if cwd:
        return str(cwd)
    get_cwd = getattr(ctx, "get_pod_cwd", None)
    if callable(get_cwd):
        value = get_cwd()
        if value:
            return str(value)
    # Derived from the workspace cwd by the one function that owns the mapping,
    # rather than rebuilt from the date and the id here. The two have to share a
    # suffix, and a second derivation is how they stop sharing it.
    return resolve_pod_cwd(conversation)


def _pod_directory_section(*, ctx: AgentContext, conversation: Conversation) -> str:
    """Where the person's files are, which is not where the sandbox is.

    Left unsaid, an agent asked to read something a person just attached looks
    in the workspace — the only directory the prompt named — finds an empty
    sandbox, and searches. Sometimes the search lands and sometimes it gives up
    saying the file does not exist, which reads as a storage bug and is really
    an agent that was never told where to look.
    """
    cwd = _pod_cwd(ctx, conversation)
    return (
        "# Pod Files\n"
        f"Your working directory in pod files is {_prompt_path(cwd)}. A "
        "pod-file path with no leading `/` resolves there, so `report.pdf` "
        f"means {_prompt_path(f'{cwd}/report.pdf')}.\n\n"
        "**Anything the person attached to a message in this conversation is "
        "in that directory.** Look there first and read it by name. It is not "
        "in the workspace sandbox.\n\n"
        "Do not go looking for it with search. Search runs over an index built "
        "after a file is stored, so a file uploaded moments ago is readable by "
        "path while search still returns nothing for it. An empty search result "
        "means *not indexed yet*, never *not there* — listing the directory or "
        "reading the path is what answers whether a file exists.\n\n"
        "This is the pod filesystem, shared with the person and durable — not "
        "the workspace, which is your own scratch space. Deliverables belong "
        "here; working files belong in the workspace."
    )


def _prompt_path(path: str) -> str:
    """A path written into instructions, in a form that cannot restructure them.

    A conversation's ``cwd`` is caller-supplied: ``metadata`` is free-form on
    both the create and update requests, and ``workspace_location_for``
    deliberately honours an explicit ``cwd`` over the derived one. It was then
    interpolated straight into a Markdown code span, so a backtick closed the
    span and a newline left the line -- and whatever followed became part of the
    agent's instructions rather than part of a path.

    A path this cannot render plainly is JSON-encoded and left outside a code
    span, which is the answer Agent Host already gives for the native working
    directory: the characters become data, the path is still stated exactly, and
    nothing is silently rewritten into a path that does not exist.
    """
    if _PLAIN_PATH.fullmatch(path):
        return f"`{path}`"
    return f"{json.dumps(path)} (JSON-encoded path)"


def _sandbox_root(cwd: str) -> str:
    """The sandbox's top-level directory, taken from the cwd this run was given.

    Every sentence below used to name ``/workspace`` outright while the cwd
    beside it came from the run. That is the same fact written twice, and the
    literal is the copy that cannot be right everywhere: an agent running as a
    native process is handed a cwd on the host, and telling it to `cd` to a
    container path it has no mount for produces a command that simply fails.

    Derived rather than configured, so there is still only one source: whatever
    root the run's own cwd is under is the root the prompt describes.
    """
    trimmed = (cwd or "").strip()
    if not trimmed.startswith("/"):
        # A relative cwd has no root to name, and returning it unchanged was a
        # hole in this very guard: the absolute branch was validated and this
        # one handed the string straight back, backticks and all, into the same
        # code spans.
        return "the working directory"
    first = trimmed.strip("/").split("/", 1)[0]
    if not first:
        return "/"
    # Every use of this sits inside a Markdown code span, and a backtick or a
    # newline in it would close the span early and turn the rest of the sentence
    # into whatever came after. Nothing is known to put one there -- the cwd is
    # assembled from a date and a slug -- but this is a value derived from
    # conversation metadata being written into instructions, and the cost of
    # being wrong about that is an instruction that reads as something else.
    #
    # A root that is not a plain path segment is not described rather than
    # described unsafely: the sentences around it still name the cwd, which is
    # what the agent needs.
    if not _PLAIN_PATH_SEGMENT.fullmatch(first):
        return "the sandbox root"
    return f"/{first}"


def _workspace_directory_section(
    *,
    ctx: AgentContext,
    conversation: Conversation,
    has_workspace_tools: bool = True,
    runs_as_remote_process: bool = False,
    has_pod_files: bool = False,
) -> str:
    cwd = _workspace_cwd(ctx, conversation)
    root = _sandbox_root(cwd)
    if not has_workspace_tools:
        return (
            "# Working Directory\n"
            "Your native tools use the directory this process started in. "
            "Agent Host supplies its exact path in Native Working Directory; "
            "it persists across conversation turns. You have no Lemma sandbox "
            "execution tools on this run. Work only in the directory you were "
            "given; do not invent a sandbox path such as `/workspace` for "
            "native tools. Tool approvals still apply."
        )
    repo = _workspace_repo(ctx)
    orientation = (
        _project_paragraph(repo)
        if repo is not None
        else (
            "An empty working directory means this is a **new conversation**, "
            "not a reset sandbox. Earlier conversations' work is still on disk "
            f"under another `{root}/c/<date>/<slug>`; list `{root}/c/` "
            "to find it. Treat prior files as gone only if a tool result says "
            "the workspace was recreated."
        )
    )
    where = (
        (
            f"Your Lemma sandbox working directory is {_prompt_path(cwd)}. Reach it **only "
            "through the Lemma tools** — `exec_command`, `execute_python` and "
            "the sandbox file tools. Native tools use the directory this "
            "process started in; native `pwd` reports that host directory. "
            "Agent Host supplies its exact path in Native Working Directory. "
            "These are separate filesystems with no automatic mount or sync. "
            f"Do not use a sandbox `{root}` path with native tools, or a host "
            "path with sandbox tools."
        )
        if runs_as_remote_process
        else (
            f"Your working directory is {_prompt_path(cwd)}. Files you write here are "
            "private to you until you upload them to pod files."
        )
    )
    # Said here, not only under `# Pod Files`, because this is the section an
    # agent acts on when it is told to go and read something: "working
    # directory" is where it looks, and a person's attachment is not there. It
    # would search the sandbox, find nothing, and sometimes conclude the file
    # did not exist.
    attachments = (
        "\n\nFiles a person **attached to this conversation are not here** — "
        "they are in pod files, under the directory named in `# Pod Files`. "
        f"Read them there rather than looking for them under `{root}`."
        if has_pod_files
        else ""
    )
    return (
        "# Working Directory\n"
        f"{where}\n\n"
        f"{orientation}{attachments}\n\n"
        f"Files under `{root}` survive an idle pause; running processes and "
        "your `execute_python` kernel do not, so don't plan around a background "
        "process living between turns. A `cd` in one `exec_command` does not "
        "carry to the next — pass `workdir` or use relative paths."
    )
