"""Connector-operation execution saga.

Mirrors ``FunctionUseCases``: built from a ``uow_factory`` + a per-phase service
builder so the DB/auth resolve phase runs inside a SHORT unit-of-work scope and
the external Composio/Lemma operation call runs with NO pooled DB connection
held. A request-scoped service/context dependency would otherwise pin one pooled
connection per in-flight connector call (every ``pod.connectors.execute(...)``
from a function routes through here), exhausting the pool under load.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable
from uuid import UUID

from fastapi import Request

from app.core.authorization.scope import current_context_scope, uow_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationExecutionResponse,
)
import httpx

from app.modules.connectors.domain.errors import (
    ConnectorDomainError,
    OperationExecutionAccessDeniedError,
    OperationExecutionInfrastructureError,
    OperationExecutionTimeoutError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.infrastructure.operation_breaker import (
    breaker_scope,
    guard as breaker_guard,
    record_failure as breaker_record_failure,
    record_success as breaker_record_success,
)
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
    ResolvedConnectorExecution,
)


class ConnectorOperationUseCases:
    """Owns the connector-operation execution saga (factory mode)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[Any], ConnectorOperationService],
        pod_file_gateway_factory: Callable[[Any], Any] | None = None,
    ):
        self._uow_factory = uow_factory
        self._build = service_builder
        # Supplied at composition, because reaching the pod datastore is where
        # the connectors and datastore modules meet. None means "no pod context",
        # and file results simply come back inline.
        self._pod_file_gateway_factory = pod_file_gateway_factory or (lambda _uow: None)

    async def execute_operation_for_auth_config(
        self,
        *,
        organization_id: UUID,
        auth_config_name: str,
        operation_name: str,
        payload: dict[str, Any],
        user_id: UUID,
        request: Request,
        auth_token: str | None = None,
        api_url: str | None = None,
        account_id: UUID | None = None,
    ) -> OperationExecutionResponse:
        # Phase 1 (short scope): build + bind the request Context (org/delegation
        # aware), resolve all DB state + authorize + resolve credentials. The
        # scope commits any OAuth-token refresh and releases the connection on
        # exit, before the external call.
        async with current_context_scope(
            self._uow_factory, request=request, user_id=user_id
        ) as scope:
            resolved = await self._build(scope.uow).resolve_execution_for_auth_config(
                user_id=user_id,
                organization_id=organization_id,
                auth_config_name=auth_config_name,
                operation_name=operation_name,
                payload=payload,
                actor=scope.ctx,
                auth_token=auth_token,
                api_url=api_url,
                account_id=account_id,
            )

        # Phase 2: the external operation call, with NO pooled connection held.
        # ``execute_resolved`` issues no DB I/O -- the gateway's connector
        # validation is skipped (``resolved.provider`` is always set) and the
        # concrete Lemma/Composio gateways are DB-free -- so this short uow never
        # checks out a connection across the (1-45s) external call. The scope only
        # supplies the service collaborator that owns the gateway + timeout +
        # error-mapping logic.
        # A provider that is down makes every caller wait the full timeout to be
        # told the same thing, and adds load to something already struggling.
        # Only infrastructure and timeout failures feed the breaker; a rejected
        # request or a stale credential is the caller's problem and must not
        # disable the operation for everyone else.
        scope_key = breaker_scope(resolved.connector_id, operation_name)
        await breaker_guard(scope_key)
        try:
            async with uow_scope(self._uow_factory) as uow:
                response = await self._build(uow).execute_resolved(resolved)
        except (
            OperationExecutionInfrastructureError,
            OperationExecutionTimeoutError,
        ):
            await breaker_record_failure(scope_key)
            raise
        except OperationExecutionUnauthorizedError:
            # The credential was rejected. Rather than refreshing before every
            # call on the chance this happens, refresh here, once, and retry
            # once. This also covers the case an expiry check never can: a
            # credential revoked at the provider while still unexpired.
            retried = await self._retry_with_refreshed_credentials(
                resolved, user_id=user_id, request=request
            )
            if retried is not None:
                response = retried
            else:
                # Still rejected after a refresh: the account is unusable until
                # the user reconnects. Flagged in a fresh short scope, then the
                # original error is re-raised unchanged.
                await self._flag_account_reauth_required(resolved)
                raise
        except OperationExecutionAccessDeniedError:
            # A scope/permission problem, not a stale credential -- refreshing
            # would not help, so flag and surface it directly.
            await self._flag_account_reauth_required(resolved)
            raise
        await breaker_record_success(scope_key)

        # Phase 3: if the result carries a file, decide what the caller actually
        # receives -- inline bytes for something small, a pod-datastore reference
        # for something large. Its own short scope, after the external call.
        return await self._capture_binary_output(
            response,
            payload=payload,
            user_id=user_id,
            request=request,
            connector_id=resolved.connector_id,
        )

    async def _capture_binary_output(
        self,
        response: OperationExecutionResponse,
        *,
        payload: dict[str, Any],
        user_id: UUID,
        request: Request,
        connector_id: str,
    ) -> OperationExecutionResponse:
        """Return a usable file, whatever shape the provider wrapped it in.

        Detection is by shape anywhere in the result rather than one envelope at
        the top level, which is why a Composio download -- nested under ``data``
        in Composio's own envelope -- now resolves at all. Persisting is decided
        by size; ``output_path`` only chooses the destination.
        """
        from app.modules.connectors.services.files.capture_writer import (
            BinaryResultWriter,
        )

        result = getattr(response, "result", None)
        # Resolve BEFORE opening a session. Finding the binary walks and
        # base64-decodes the whole third-party response, and for a URL-sourced
        # result it downloads the file too — seconds of work proportional to
        # something we do not control. Only persisting it needs the database.
        writer = BinaryResultWriter(None)
        resolved = await writer.resolve(result)
        if resolved is None:
            return response

        async with current_context_scope(
            self._uow_factory, request=request, user_id=user_id
        ) as scope:
            captured = await BinaryResultWriter(
                self._pod_file_gateway_factory(scope.uow)
            ).capture(
                result,
                connector_id=connector_id,
                pod_id=getattr(scope.ctx, "pod_id", None),
                ctx=scope.ctx,
                output_path=(payload or {}).get("output_path"),
                resolved=resolved,
            )
        return OperationExecutionResponse(result=captured)

    async def _retry_with_refreshed_credentials(
        self,
        resolved: ResolvedConnectorExecution,
        *,
        user_id: UUID,
        request: Request,
    ) -> OperationExecutionResponse | None:
        """Refresh the credential once and retry once; None if that did not help.

        Bounded deliberately at one attempt: a provider that rejects a
        freshly-minted credential is telling us the account needs reconnecting,
        and retrying past that just multiplies latency on a call that is going
        to fail anyway.
        """
        if resolved.account_id is None or resolved.account_user_id is None:
            return None
        try:
            async with current_context_scope(
                self._uow_factory, request=request, user_id=user_id
            ) as scope:
                service = self._build(scope.uow)
                if service.connector_service is None:
                    return None
                refreshed = await service.connector_service.get_account_credentials(
                    resolved.account_id,
                    resolved.account_user_id,
                    resolved.organization_id,
                    force_refresh=True,
                )
                credentials = refreshed.model_dump(exclude_none=True)
        except (ConnectorDomainError, httpx.HTTPError, OSError, TimeoutError):
            # Refresh itself failed: no refresh token on the account, or the
            # provider is unreachable. Fall back to the reauth path rather than
            # masking the original rejection. Anything outside this set is a bug
            # here, not an upstream problem, and should surface as one.
            return None

        retry = replace(resolved, third_party_credentials=credentials)
        try:
            async with uow_scope(self._uow_factory) as uow:
                return await self._build(uow).execute_resolved(retry)
        except OperationExecutionUnauthorizedError:
            return None

    async def _flag_account_reauth_required(
        self, resolved: ResolvedConnectorExecution
    ) -> None:
        if resolved.account_id is None or resolved.account_user_id is None:
            return
        async with uow_scope(self._uow_factory) as uow:
            connector_service = self._build(uow).connector_service
            if connector_service is None:
                return
            await connector_service.mark_account_reauth_required(
                resolved.account_id,
                resolved.account_user_id,
                resolved.organization_id,
            )
