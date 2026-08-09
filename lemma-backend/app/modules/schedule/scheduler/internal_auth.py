"""The shared secret between the backend and the scheduler's job API.

The job API is an internal control plane: it can create, pause and delete the
jobs that drive every schedule, workflow wake-up and agent message in the pod.
Nothing outside the platform should ever reach it, so it authenticates callers
with a service token rather than a user session.
"""

from __future__ import annotations

import secrets

from pydantic import SecretStr

from app.core.config import reveal_secret
from app.modules.schedule.config import schedule_settings


def get_internal_token() -> str:
    """The configured service token, or empty when the operator set none."""
    return reveal_secret(schedule_settings.scheduler_internal_token) or ""


def ensure_internal_token() -> str:
    """Return the service token, minting a process-local one if none is set.

    Only sound where the caller and the job API share a process, as in the
    single-process standalone assembly: a token minted here lives in memory and
    two separate processes could never agree on it. A split deployment has to
    configure ``SCHEDULER_INTERNAL_TOKEN`` explicitly, and
    ``app/scheduler.py`` refuses to start without it.
    """
    existing = get_internal_token()
    if existing:
        return existing
    minted = secrets.token_urlsafe(32)
    schedule_settings.scheduler_internal_token = SecretStr(minted)
    return minted
