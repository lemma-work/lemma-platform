"""Deciding that a failing function runtime is dead, and cleaning up after one.

One pod's function sandbox serves every run of every function in that pod at
once. Destroying it on the evidence of one failed invocation turned a single
transient error -- one 5xx, one dropped connection -- into the loss of every
run in flight on it, each of which then sat until its deadline plus the
callback grace before anything noticed. So the failure only nominates the
sandbox; the runtime's own health probe decides. A runtime that still answers
it keeps serving, and the one invocation that failed is reported on its own.

When the probe agrees and the sandbox is destroyed, the asynchronous runs that
were on it are failed at once rather than left for the deadline sweep: nothing
is left to report a result for them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urljoin
from uuid import UUID

import httpx

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
)
from app.modules.function.application.function_runtime_route_resolver import (
    FunctionRuntimeRouteResolver,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)

logger = get_logger(__name__)

#: Long enough for a loaded but live runtime to answer a request that does no
#: work; short enough that a dead one does not hold up the failing invocation.
HEALTH_PROBE_TIMEOUT_SECONDS = 3.0

ORPHANED_RUN_ERROR = (
    "Function sandbox was replaced while this run was in flight; it may have "
    "partly run and was not retried"
)

#: Orphaned runs failed per transaction when a sandbox is destroyed.
_ORPHANED_RUNS_PAGE = 200


class FunctionRuntimeQuarantine:
    def __init__(
        self,
        *,
        routes: FunctionRuntimeRouteResolver,
        uow_factory: UnitOfWorkFactory,
        runtime_http_client_factory: Callable[[], httpx.AsyncClient],
    ) -> None:
        self._routes = routes
        self._uow_factory = uow_factory
        self._runtime_http_client_factory = runtime_http_client_factory

    async def after_failure(
        self,
        pod_id: UUID,
        endpoint: FunctionRuntimeEndpoint,
    ) -> bool:
        """Quarantine the sandbox only if its runtime fails the probe too.

        Returns whether it was quarantined.
        """
        if await self._healthy(endpoint):
            logger.info(
                "function.runtime.quarantine_skipped_healthy",
                pod_id=str(pod_id),
            )
            return False
        destroyed_at = datetime.now(timezone.utc)
        # False also when the sandbox had already been replaced: the runs in
        # flight now are on the replacement, and failing them would be wrong.
        if await self._routes.quarantine(pod_id, endpoint):
            await self._fail_orphaned_runs(pod_id, started_before=destroyed_at)
        return True

    async def _healthy(self, endpoint: FunctionRuntimeEndpoint) -> bool:
        try:
            response = await self._runtime_http_client_factory().get(
                urljoin(endpoint.url, "healthz"),
                headers=endpoint.headers(),
                timeout=httpx.Timeout(HEALTH_PROBE_TIMEOUT_SECONDS),
            )
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("ready") is True

    async def _fail_orphaned_runs(
        self,
        pod_id: UUID,
        *,
        started_before: datetime,
    ) -> None:
        # In pages, each its own transaction: the pod's run gate keeps the
        # in-flight set far below a page, but nothing here should hold an
        # unbounded set of row locks if it ever is not.
        total = 0
        while True:
            async with self._uow_factory() as uow:
                failed = await FunctionExecutionRepository(uow).fail_in_flight_on_pod(
                    pod_id,
                    started_before=started_before,
                    error=ORPHANED_RUN_ERROR,
                    limit=_ORPHANED_RUNS_PAGE,
                )
            total += len(failed)
            # A failed run leaves RUNNING, so the next page is new rows; a row
            # another transaction holds is skipped, never returned again.
            if len(failed) < _ORPHANED_RUNS_PAGE:
                break
        if total:
            logger.warning(
                "function.runtime.orphaned_runs_failed",
                pod_id=str(pod_id),
                run_count=total,
            )
