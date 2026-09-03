from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from ..errors import LemmaConfigError
from ..transport import MISSING, LemmaTransport


def as_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class Resource:
    def __init__(self, transport: LemmaTransport):
        self._transport = transport

    @property
    def generated(self) -> Any:
        return self._transport.generated

    def _call(
        self,
        endpoint: Any,
        *path_args: Any,
        body: Any = MISSING,
        body_model: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._transport.call(
            endpoint,
            *path_args,
            body=body,
            body_model=body_model,
            **kwargs,
        )

    def _stream(
        self,
        endpoint: Any,
        *path_args: Any,
        read_timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Open a streaming endpoint. The caller owns closing the response."""
        return self._transport.stream(
            endpoint, *path_args, read_timeout=read_timeout, **kwargs
        )


class BoundResource(Resource):
    def __init__(
        self,
        transport: LemmaTransport,
        *,
        org_id: str | UUID | None = None,
        pod_id: str | UUID | None = None,
    ) -> None:
        super().__init__(transport)
        self.org_id = org_id
        self.pod_id = pod_id

    def _org_uuid(self) -> UUID:
        if self.org_id is None:
            raise LemmaConfigError("org_id is required for this operation")
        return as_uuid(self.org_id)

    def _pod_uuid(self) -> UUID:
        if self.pod_id is None:
            raise LemmaConfigError("pod_id is required for this operation")
        return as_uuid(self.pod_id)
