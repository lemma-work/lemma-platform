"""Files in and out of one operation call, for every caller that runs one.

Two callers run connector operations -- the REST route and the agent's
`run_connector_operation` tool -- and they had drifted: only the route captured
file results, and neither read file inputs. This is the one implementation
both use, so the agent path cannot fall behind again.

Three steps, each placed around the provider call rather than inside it:

* :meth:`OperationFiles.prepare` -- before: take Lemma's ``output_path`` out of
  the provider payload and read each file reference, as the caller.
* :func:`find_file_result` -- after, holding no session: find a file in the
  result and fetch it if it is a URL.
* :meth:`OperationFiles.capture` -- after, in a session: inline it, or land it
  in the pod.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationExecutionResponse,
)
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution
from app.modules.connectors.domain.ports import PodFileGatewayPort
from app.modules.connectors.services.files.capture import BinaryCandidate
from app.modules.connectors.services.files.capture_writer import BinaryResultWriter
from app.modules.connectors.services.files.input_resolver import (
    FileInputResolver,
    contains_file_reference,
    split_output_path,
)

FoundFile = tuple[BinaryCandidate, bytes]


def split_lemma_arguments(
    resolved: ResolvedConnectorExecution,
) -> ResolvedConnectorExecution:
    """The plan with ``output_path`` moved off the provider payload.

    Needs no session, so a caller with no file inputs never opens one.
    """
    payload, output_path = split_output_path(resolved.input_schema, resolved.payload)
    if payload == resolved.payload and output_path is None:
        return resolved
    return replace(resolved, payload=payload, requested_output_path=output_path)


def needs_file_inputs(resolved: ResolvedConnectorExecution) -> bool:
    return contains_file_reference(resolved.input_schema, resolved.payload)


async def find_file_result(response: OperationExecutionResponse) -> FoundFile | None:
    """The file a result carries, with its bytes. Touches no database."""
    return await BinaryResultWriter(None).resolve(response.result)


class OperationFiles:
    """Pod-file reads and writes for one call, as one caller, in one pod."""

    def __init__(
        self,
        gateway: PodFileGatewayPort | None,
        *,
        pod_id: UUID | None,
        ctx: Context,
    ) -> None:
        self._gateway = gateway
        self._pod_id = pod_id
        self._ctx = ctx

    async def prepare(
        self, resolved: ResolvedConnectorExecution
    ) -> ResolvedConnectorExecution:
        """``output_path`` split out, and each file reference read."""
        resolved = split_lemma_arguments(resolved)
        if not needs_file_inputs(resolved):
            return resolved
        payload = await FileInputResolver(
            self._gateway, pod_id=self._pod_id, ctx=self._ctx
        ).resolve(resolved.input_schema, resolved.payload)
        return replace(resolved, payload=payload)

    async def capture(
        self,
        response: OperationExecutionResponse,
        found: FoundFile,
        *,
        connector_id: str,
        output_path: str | None,
    ) -> OperationExecutionResponse:
        """The result with its file inline, or landed in the pod."""
        captured = await BinaryResultWriter(self._gateway).capture(
            response.result,
            connector_id=connector_id,
            pod_id=self._pod_id,
            ctx=self._ctx,
            output_path=output_path,
            resolved=found,
        )
        return OperationExecutionResponse(result=captured)


__all__ = [
    "FoundFile",
    "OperationFiles",
    "find_file_result",
    "needs_file_inputs",
    "split_lemma_arguments",
]
