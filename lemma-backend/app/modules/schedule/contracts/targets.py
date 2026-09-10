"""What a schedule needs to know about the thing it fires.

Value object only, and no imports past the standard library, so a module that
answers the lookup pays nothing for the rest of `schedule`.

It was declared in `schedule/domain/interfaces.py`, which no other module may
import -- so the two modules that own the rows could not name the type they
were being asked to produce, and the mapping had to be written somewhere
neither of them was. That somewhere was
`app/composition/schedule_targets.py`, which built `AgentRepository` and
`SqlAlchemyWorkflowRepository` from outside both modules and held the knowledge
of which of their fields a schedule target is made of. Publishing the type is
what lets each provider answer for its own rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ScheduleTarget:
    id: UUID
    pod_id: UUID
    name: str
    #: The target's *standing* instruction -- what it is for, as against what a
    #: firing is for. A target without one has to be told by the schedule, which
    #: is the rule `validate_target_instruction` enforces; the pod's own
    #: assistant is the only agent that can have none.
    instruction: str | None = None
    is_global_workflow: bool = False
    event_trigger_id: str | None = None
    event_trigger_config: dict[str, object] | None = None


__all__ = ["ScheduleTarget"]
