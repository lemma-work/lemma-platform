"""The credential a run that acts for no member calls back with.

A member's run gets a delegated session for that member, cached across runs
(``function_session_token_cache``). A contact's run has no member to mint a
session for, so it gets a function-run token instead (see
``core/authorization/function_run``). Nothing is cached: the token names this
one run, and lives as long as the run may still call back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.authorization.function_run import (
    FunctionRunClaims,
    mint_function_run_token,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
)
from app.modules.function.domain.entities import FunctionExecutionDispatch

#: How long the token outlives the latest moment the run may still call back,
#: so a callback sent at that moment is not refused in flight.
RUN_TOKEN_MARGIN_SECONDS = 60


def function_run_token(
    dispatch: FunctionExecutionDispatch,
    *,
    required_until: datetime,
    now: datetime | None = None,
) -> FunctionSessionToken:
    expires_at = required_until + timedelta(seconds=RUN_TOKEN_MARGIN_SECONDS)
    issued = now or datetime.now(timezone.utc)
    value = mint_function_run_token(
        FunctionRunClaims(
            run_id=dispatch.run_id,
            function_id=dispatch.function_id,
            pod_id=dispatch.pod_id,
            revision_hash=dispatch.revision_hash,
            contact_id=dispatch.contact_id,
        ),
        ttl_seconds=max(1, int((expires_at - issued).total_seconds())),
    )
    return FunctionSessionToken(value=value, expires_at=expires_at)
