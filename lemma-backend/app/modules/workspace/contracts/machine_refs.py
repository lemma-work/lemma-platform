"""Machine refs, for a module that stores one: an agent's `machine` field."""

from __future__ import annotations

from app.modules.workspace.domain.machine import MachineRef


def parse_machine_ref(value: str) -> str:
    """The ref in its canonical spelling; ``ValueError`` with a sentence if it
    is not one."""
    return str(MachineRef.parse(value))


__all__ = ["parse_machine_ref"]
