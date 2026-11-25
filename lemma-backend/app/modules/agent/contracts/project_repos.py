"""What another module may know about a repository an agent works in.

A schedule names one in its config so its runs start in a checkout of it; the
name is held to the same rules a person's pick is (``owner/name``, no third
segment, no option-shaped or ``..`` name), checked where the schedule is saved
rather than discovered when the clone fails at six in the morning.
"""

from __future__ import annotations

from app.modules.agent.domain.project_repo import parse_repository_name


def is_repository_name(value: object) -> bool:
    """Whether ``value`` is a GitHub repository as ``owner/name``."""
    return isinstance(value, str) and parse_repository_name(value) is not None
