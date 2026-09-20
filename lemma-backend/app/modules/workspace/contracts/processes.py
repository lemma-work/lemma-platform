"""Reading a sandbox process's state, for modules that may not import services.

`agent` needs this for a durable PROCESS wait: the run is suspended and a worker
has to ask, every few seconds, whether the thing it is waiting on has ended.
Published here because `contracts/` is the module's allowed outward surface —
reaching into `services/` from another module is what the architecture check
exists to stop.
"""

from __future__ import annotations

from app.modules.workspace.services.process_probe import (
    ProcessProbe,
    ProcessProbeStatus,
    probe_process,
)

__all__ = ["ProcessProbe", "ProcessProbeStatus", "probe_process"]
