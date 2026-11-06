"""What the agent module's link route needs to serve a connected machine.

The link is the agent module's; a first frame naming ``role: "compute"`` is a
connected machine, which this module serves (docs/architecture/connected-
machines.md). Imported when the first such frame arrives, not at startup.
"""

from __future__ import annotations

from app.modules.workspace.services.machine_link.handler import compute_link_handler

__all__ = ["compute_link_handler"]
