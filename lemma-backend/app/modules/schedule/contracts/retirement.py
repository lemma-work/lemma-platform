"""Standing schedules down, as a public command.

A submodule for the same reason as its sibling in `connectors`: it reaches the
model layer, and `contracts/__init__` is imported by anything that wants any
contract at all.
"""

from app.modules.schedule.services.schedule_retirement import (
    deactivate_matching_schedules,
)

__all__ = ["deactivate_matching_schedules"]
