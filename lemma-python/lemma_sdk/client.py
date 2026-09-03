from __future__ import annotations

from dataclasses import replace
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import LemmaAPIError, LemmaConfigError
from .settings import load_settings
from .transport import LemmaTransport

if TYPE_CHECKING:
    from .pod import Pod
    from .resources import (
        AgentHosts,
        BoundConnectors,
        BoundOrg,
        BoundOrgRuntime,
        BoundPods,
        Orgs,
        Tools,
        User,
    )


class Lemma:
    def __init__(
        self,
        *,
        org_id: str | None = None,
        pod_id: str | None = None,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        verify_ssl: bool | None = None,
        server: str | None = None,
        config_path: Path | None = None,
        lemma: "Lemma | None" = None,
    ) -> None:
        # ``lemma=`` makes this a view of another client -- same endpoint, same
        # credential, same connection pool -- the way ``Pod(lemma=...)`` does.
        # Only the scope changes, so nothing is re-resolved and nothing new is
        # opened; see :meth:`for_org`.
        self._owns_transport = lemma is None
        if lemma is not None:
            self.settings = replace(
                lemma.settings,
                org_id=org_id if org_id is not None else lemma.settings.org_id,
                pod_id=pod_id if pod_id is not None else lemma.settings.pod_id,
            )
            self._transport = lemma._transport
        else:
            self.settings = load_settings(
                base_url=base_url,
                token=token,
                org_id=org_id,
                pod_id=pod_id,
                timeout=timeout,
                verify_ssl=verify_ssl,
                server=server,
                config_path=config_path,
            )
            self._transport = LemmaTransport(
                base_url=self.settings.base_url,
                token=self.settings.token,
                timeout=self.settings.timeout,
                verify_ssl=self.settings.verify_ssl,
            )
        self.org_id = self.settings.org_id
        self.default_pod_id = self.settings.pod_id

    # Resources are exposed as cached properties so a command only imports and
    # instantiates the handful it touches, instead of loading every resource
    # module (and its generated API/model slice) on client construction.
    @cached_property
    def orgs(self) -> "Orgs":
        from .resources import Orgs

        return Orgs(self._transport)

    @cached_property
    def user(self) -> "User":
        from .resources import User

        return User(self._transport)

    @cached_property
    def org(self) -> "BoundOrg":
        from .resources import BoundOrg

        return BoundOrg(self._transport, org_id=self.org_id)

    @cached_property
    def pods(self) -> "BoundPods":
        from .resources import BoundPods

        return BoundPods(self._transport, org_id=self.org_id, lemma=self)

    @cached_property
    def connectors(self) -> "BoundConnectors":
        from .resources import BoundConnectors

        return BoundConnectors(self._transport, org_id=self.org_id)

    @cached_property
    def tools(self) -> "Tools":
        from .resources import Tools

        return Tools(self._transport)

    @cached_property
    def agent_hosts(self) -> "AgentHosts":
        from .resources import AgentHosts

        return AgentHosts(self._transport)

    @cached_property
    def org_runtime(self) -> "BoundOrgRuntime":
        from .resources import BoundOrgRuntime

        return BoundOrgRuntime(self._transport, org_id=self.org_id)

    @classmethod
    def from_env(
        cls,
        *,
        org_id: str | None = None,
        pod_id: str | None = None,
        server: str | None = None,
        config_path: Path | None = None,
    ) -> "Lemma":
        return cls(org_id=org_id, pod_id=pod_id, server=server, config_path=config_path)

    @property
    def generated(self) -> Any:
        return self._transport.generated

    def pod(self, pod_id: str | None = None, *, org_id: str | None = None) -> "Pod":
        from .pod import Pod

        resolved_pod_id = pod_id or self.default_pod_id
        if not resolved_pod_id:
            raise LemmaConfigError(
                "pod_id is required. Pass pod_id or set LEMMA_POD_ID."
            )
        return Pod(
            pod_id=resolved_pod_id,
            org_id=org_id or self.org_id,
            lemma=self,
        )

    def for_org(self, org_id: str) -> "Lemma":
        """This client, scoped to another organization, over the same connection.

        A derived client that built its own transport would be invisible to this
        one's ``close()``: a process that walks organizations would then hold a
        connection pool per organization, with nothing to reclaim them.
        """
        return Lemma(org_id=org_id, lemma=self)

    def close(self) -> None:
        # A view from ``for_org`` borrows the pool; closing it must not take the
        # parent's connections down with it.
        if self._owns_transport:
            self._transport.close()

    def __enter__(self) -> "Lemma":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


__all__ = ["Lemma", "LemmaAPIError", "LemmaConfigError"]
