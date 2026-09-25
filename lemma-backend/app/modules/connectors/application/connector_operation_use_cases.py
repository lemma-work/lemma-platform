"""Connector-operation execution saga.

Mirrors ``FunctionUseCases``: built from a ``uow_factory`` + a per-phase service
builder so the DB/auth resolve phase runs inside a SHORT unit-of-work scope and
the external Composio/Lemma operation call runs with NO pooled DB connection
held. A request-scoped service/context dependency would otherwise pin one pooled
connection per in-flight connector call (every ``pod.connectors.execute(...)``
from a function routes through here), exhausting the pool under load.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any, Callable
from uuid import UUID

import httpx
from fastapi import Request

from app.core.authorization.scope import (
    UowContext,
    current_context_scope,
    pod_context_scope,
    uow_scope,
)
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationExecutionResponse,
)
from app.modules.connectors.domain.errors import (
    ConnectorDomainError,
    ConnectorValidationError,
    OperationExecutionAccessDeniedError,
    OperationExecutionInfrastructureError,
    OperationExecutionTimeoutError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.infrastructure.operation_breaker import (
    breaker_scope,
)
from app.modules.connectors.infrastructure.operation_breaker import (
    guard as breaker_guard,
)
from app.modules.connectors.infrastructure.operation_breaker import (
    record_failure as breaker_record_failure,
)
from app.modules.connectors.infrastructure.operation_breaker import (
    record_success as breaker_record_success,
)
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
    ResolvedConnectorExecution,
)


def _pod_for_files(context_pod: UUID | None, requested: UUID | None) -> UUID | None:
    """The pod file references resolve in: the delegated one, or the named one.

    A pod function's delegated token already fixes its pod; naming a different
    one there is refused rather than quietly preferred, since it would read the
    files of a pod the token was never issued for.
    """
    if requested is not None and context_pod is not None and requested != context_pod:
        raise ConnectorValidationError(
            "pod_id does not match the pod this call is running in.",
            details={"reason": "pod_mismatch"},
        )
    return context_pod or requested


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
        account_id: UUID | None = None,
        pod_id: UUID | None = None,
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
                account_id=account_id,
            )
            pod_id = _pod_for_files(getattr(scope.ctx, "pod_id", None), pod_id)

        # Phase 1b: read the files the arguments name, as the caller, in their
        # pod. Skipped outright -- no scope, no read -- when there are none,
        # which is nearly every call.
        resolved = await self._materialize_file_inputs(
            resolved, user_id=user_id, request=request, pod_id=pod_id
        )
        output_path = resolved.requested_output_path

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
        scope_key = breaker_scope(
            resolved.connector_id, operation_name, resolved.organization_id
        )
        await breaker_guard(scope_key)
        try:
            response = await self._attempt_with_credential_refresh(
                resolved, user_id=user_id, request=request
            )
        except (
            OperationExecutionInfrastructureError,
            OperationExecutionTimeoutError,
        ):
            # Wraps the credential retry as well as the first call. Wrapping only
            # the first call left a hole: a 401 that refreshed and then timed out
            # raised from inside the handler, past this clause, and the breaker
            # never saw it -- so the failure mode most likely to be *systemic*
            # (every account's credential rejected because the provider's token
            # endpoint is down, each one then retrying into the same outage) was
            # the one failure mode that could not trip it.
            await breaker_record_failure(scope_key)
            raise
        await breaker_record_success(scope_key)

        # Phase 3: if the result carries a file, decide what the caller actually
        # receives -- inline bytes for something small, a pod-datastore reference
        # for something large. Its own short scope, after the external call.
        return await self._capture_binary_output(
            response,
            output_path=output_path,
            user_id=user_id,
            request=request,
            connector_id=resolved.connector_id,
            pod_id=pod_id,
        )

    def _scope(
        self, *, request: Request, user_id: UUID, pod_id: UUID | None
    ) -> AbstractAsyncContextManager[UowContext]:
        """The caller's context, in the pod the call named when it named one.

        An org-level call made by a person carries no pod, so `/me/...` had
        nothing to resolve against. Naming one builds the pod context, which is
        also where the person's membership of that pod is checked.
        """
        if pod_id is not None:
            return pod_context_scope(
                self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
            )
        return current_context_scope(
            self._uow_factory, request=request, user_id=user_id
        )

    async def _materialize_file_inputs(
        self,
        resolved: ResolvedConnectorExecution,
        *,
        user_id: UUID,
        request: Request,
        pod_id: UUID | None,
    ) -> ResolvedConnectorExecution:
        from app.modules.connectors.services.files.operation_files import (
            OperationFiles,
            needs_file_inputs,
            split_lemma_arguments,
        )

        resolved = split_lemma_arguments(resolved)
        if not needs_file_inputs(resolved):
            return resolved
        async with self._scope(
            request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            return await OperationFiles(
                self._pod_file_gateway_factory(scope.uow), pod_id=pod_id, ctx=scope.ctx
            ).prepare(resolved)

    async def _capture_binary_output(
        self,
        response: OperationExecutionResponse,
        *,
        output_path: str | None,
        user_id: UUID,
        request: Request,
        connector_id: str,
        pod_id: UUID | None,
    ) -> OperationExecutionResponse:
        """Return a usable file, whatever shape the provider wrapped it in.

        Detection is by shape anywhere in the result rather than one envelope at
        the top level, which is why a Composio download -- nested under ``data``
        in Composio's own envelope -- now resolves at all. Persisting is decided
        by size; ``output_path`` only chooses the destination.
        """
        from app.modules.connectors.services.files.operation_files import (
            OperationFiles,
            find_file_result,
        )

        # Found BEFORE opening a session. Finding the binary walks and
        # base64-decodes the whole third-party response, and for a URL-sourced
        # result it downloads the file too — seconds of work proportional to
        # something we do not control. Only persisting it needs the database.
        found = await find_file_result(response)
        if found is None:
            return response
        async with self._scope(
            request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            return await OperationFiles(
                self._pod_file_gateway_factory(scope.uow), pod_id=pod_id, ctx=scope.ctx
            ).capture(
                response, found, connector_id=connector_id, output_path=output_path
            )

    async def _attempt_with_credential_refresh(
        self,
        resolved: ResolvedConnectorExecution,
        *,
        user_id: UUID,
        request: Request,
    ) -> OperationExecutionResponse:
        """Run the operation, refreshing the credential once if it is rejected.

        One unit so the caller has a single place to judge "did this attempt
        fail because the provider is unwell", which is the question the breaker
        asks. The credential handling underneath is bookkeeping about *this*
        account and is nobody else's business.
        """
        try:
            async with uow_scope(self._uow_factory) as uow:
                return await self._build(uow).execute_resolved(resolved)
        except OperationExecutionUnauthorizedError:
            # The credential was rejected. Rather than refreshing before every
            # call on the chance this happens, refresh here, once, and retry
            # once. This also covers the case an expiry check never can: a
            # credential revoked at the provider while still unexpired.
            retried = await self._retry_with_refreshed_credentials(
                resolved, user_id=user_id, request=request
            )
            if retried is not None:
                return retried
            # Still rejected after a refresh: the account is unusable until the
            # user reconnects. Flagged in a fresh short scope, then the original
            # error is re-raised unchanged.
            await self._flag_account_reauth_required(resolved)
            raise
        except OperationExecutionAccessDeniedError:
            # A scope/permission problem, not a stale credential -- refreshing
            # would not help, so flag and surface it directly.
            await self._flag_account_reauth_required(resolved)
            raise

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
        except ConnectorDomainError, httpx.HTTPError, OSError, TimeoutError:
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
