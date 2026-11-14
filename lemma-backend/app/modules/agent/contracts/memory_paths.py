"""Where a pod keeps what it has been taught: its shared memory.

Published for a module that writes a pod's first notes -- starting a Lemma
from a template writes the template's own rules there -- so it lands on
exactly the paths every agent's brief reads back.
"""

from __future__ import annotations

from app.modules.agent.domain.agent_memory_paths import (
    POD_MEMORY_FOLDER,
    POD_MEMORY_INDEX,
)

__all__ = ["POD_MEMORY_FOLDER", "POD_MEMORY_INDEX"]
