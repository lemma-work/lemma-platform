from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .errors import LemmaConfigError
from .settings import load_settings
from .transport import MISSING as _MISSING, LemmaTransport

if TYPE_CHECKING:
    from .resources import (
        BoundConnectors,
        PodAgents,
        PodConversations,
        PodApps,
        PodFiles,
        PodFunctions,
        PodMembers,
        PodQueries,
        PodRecords,
        PodSchedules,
        PodSurfaces,
        PodTables,
        PodWorkflows,
        Table,
    )


class Pod:
    def __init__(
        self,
        pod_id: str,
        *,
        org_id: str | None = None,
        lemma: Any | None = None,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        verify_ssl: bool | None = None,
        server: str | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.pod_id = pod_id
        self.org_id = org_id
        self._lemma = lemma
        self._owns_transport = lemma is None
        if lemma is not None:
            self._transport = lemma._transport
            if self.org_id is None:
                self.org_id = lemma.org_id
        else:
            settings = load_settings(
                base_url=base_url,
                token=token,
                org_id=org_id,
                pod_id=pod_id,
                timeout=timeout,
                verify_ssl=verify_ssl,
                server=server,
                config_path=config_path,
            )
            self.org_id = settings.org_id
            self._transport = LemmaTransport(
                base_url=settings.base_url,
                token=settings.token,
                timeout=settings.timeout,
                verify_ssl=settings.verify_ssl,
            )

    # Each pod-scoped resource is a cached property: a command only imports and
    # builds the one(s) it uses rather than the whole resource/model tree.
    def _resource(self, cls):  # type: ignore[no-untyped-def]
        return cls(self._transport, pod_id=self.pod_id, org_id=self.org_id)

    @cached_property
    def tables(self) -> "PodTables":
        from .resources import PodTables

        return self._resource(PodTables)

    @cached_property
    def records(self) -> "PodRecords":
        from .resources import PodRecords

        return self._resource(PodRecords)

    @cached_property
    def queries(self) -> "PodQueries":
        from .resources import PodQueries

        return self._resource(PodQueries)

    @cached_property
    def files(self) -> "PodFiles":
        from .resources import PodFiles

        return self._resource(PodFiles)

    @cached_property
    def functions(self) -> "PodFunctions":
        from .resources import PodFunctions

        return self._resource(PodFunctions)

    @cached_property
    def members(self) -> "PodMembers":
        from .resources import PodMembers

        return self._resource(PodMembers)

    @cached_property
    def agents(self) -> "PodAgents":
        from .resources import PodAgents

        return self._resource(PodAgents)

    @cached_property
    def workflows(self) -> "PodWorkflows":
        from .resources import PodWorkflows

        return self._resource(PodWorkflows)

    @cached_property
    def schedules(self) -> "PodSchedules":
        from .resources import PodSchedules

        return self._resource(PodSchedules)

    @cached_property
    def conversations(self) -> "PodConversations":
        from .resources import PodConversations

        return self._resource(PodConversations)

    @cached_property
    def apps(self) -> "PodApps":
        from .resources import PodApps

        return self._resource(PodApps)

    @cached_property
    def surfaces(self) -> "PodSurfaces":
        from .resources import PodSurfaces

        return self._resource(PodSurfaces)

    @cached_property
    def connectors(self) -> "BoundConnectors":
        from .resources import BoundConnectors

        return self._resource(BoundConnectors)

    @classmethod
    def from_env(
        cls,
        *,
        pod_id: str | None = None,
        org_id: str | None = None,
        server: str | None = None,
        config_path: Path | None = None,
    ) -> "Pod":
        # Function invocations use task/thread-local runtime context so
        # concurrent users never race through process-global environment
        # credentials. Outside the runtime this remains the CLI/env constructor.
        from .runtime import current_function_invocation

        invocation = current_function_invocation()
        if invocation is not None:
            selected_pod = (
                UUID(str(pod_id)) if pod_id is not None else invocation.pod_id
            )
            if selected_pod != invocation.pod_id:
                raise LemmaConfigError(
                    "pod_id cannot override the active function invocation pod"
                )
            return invocation.register(
                cls(
                    pod_id=str(invocation.pod_id),
                    org_id=(
                        org_id
                        or (
                            str(invocation.organization_id)
                            if invocation.organization_id is not None
                            else None
                        )
                    ),
                    base_url=invocation.base_url,
                    token=invocation.token,
                )
            )
        settings = load_settings(
            org_id=org_id,
            pod_id=pod_id,
            server=server,
            config_path=config_path,
        )
        if not settings.pod_id:
            raise LemmaConfigError(
                "pod_id is required. Pass pod_id or set LEMMA_POD_ID."
            )
        return cls(
            pod_id=settings.pod_id,
            org_id=settings.org_id,
            base_url=settings.base_url,
            token=settings.token,
            timeout=settings.timeout,
            verify_ssl=settings.verify_ssl,
            server=settings.server,
            config_path=settings.config_path,
        )

    @property
    def generated(self) -> Any:
        return self._transport.generated

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = _MISSING,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Escape hatch: make a raw authenticated request for endpoints the typed
        resources don't cover yet (mirrors the TS `client.request`). Returns
        parsed JSON or text. ``path`` is relative, e.g. ``/pods/<id>/...``."""
        return self._transport.request(
            method, path, params=params, json_body=json, headers=headers
        )

    def table(self, name: str) -> "Table":
        from .resources import Table

        return Table(self.records, name)

    def query(self, sql: str) -> Any:
        return self.queries.run(sql)

    def close(self) -> None:
        if self._owns_transport:
            self._transport.close()

    def __enter__(self) -> "Pod":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
