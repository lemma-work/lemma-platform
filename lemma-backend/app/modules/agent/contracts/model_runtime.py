"""Running one thing on the system model, from outside `agent`.

The operation itself lives beside the model factory
(``infrastructure/providers/system_model``); this is its published name. A
submodule rather than `contracts/__init__.py`, which is a leaf: this reaches
the runtime profile service, and everything importing any agent contract would
otherwise pay for it. Same reason as `automation.py` next door.
"""

from app.modules.agent.infrastructure.providers.system_model import (
    SystemModelRuntime,
    resolve_system_runtime,
)

__all__ = ["SystemModelRuntime", "resolve_system_runtime"]
