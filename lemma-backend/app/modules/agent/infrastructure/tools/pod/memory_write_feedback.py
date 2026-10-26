"""What a write into memory is told about its own size.

Memory is read back into later runs: the four AGENTS.md indexes into every
run's context, and topic and journal files whenever they matter. Nothing trims
them afterwards, and an agent told to keep notes keeps appending -- so the one
moment to say a file has grown past what is worth re-reading is the write
that grew it, in the tool result the agent is looking at.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_memory_paths import agent_memory_paths_for_name
from app.modules.agent.services.context_brief.memory import guaranteed_index_chars
from app.modules.agent.infrastructure.tools.pod.pod_paths import to_me_path

_SHARED_MEMORY_ROOT = "/memory/"


def memory_write_warning(
    stored_path: str,
    *,
    user_id: UUID,
    agent_name: str | None,
    content_length: int,
) -> str | None:
    """A warning for a memory index or memory file written past its size, or None."""
    path = to_me_path(stored_path, user_id)
    paths = agent_memory_paths_for_name(agent_name)
    indexes = {
        paths.pod_index,
        paths.pod_agent_index,
        paths.personal_index,
        paths.personal_agent_index,
    }
    if path in indexes:
        room = guaranteed_index_chars(paths)
        if content_length <= room:
            return None
        return (
            f"This index is {content_length} characters. With all four memory "
            f"indexes written, only about the first {room} of each reach your "
            "context, and the rest is cut on every run. Move the detail into a "
            "topic file and leave a one-line pointer here."
        )
    in_memory = path.startswith(
        (_SHARED_MEMORY_ROOT, f"{paths.personal_agent_folder}/")
    )
    limit = agent_settings.agent_memory_file_max_chars
    if not in_memory or content_length <= limit:
        return None
    return (
        f"This memory file is {content_length} characters, past the {limit} a "
        "memory file should stay under: it is read back in full whenever it is "
        "relevant. Rewrite it to what is still true and still useful -- drop "
        "superseded entries rather than keeping their history -- or split it "
        "by topic."
    )
