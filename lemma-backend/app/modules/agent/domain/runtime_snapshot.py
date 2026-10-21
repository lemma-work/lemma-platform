"""What a run knows about its resolved runtime, as one typed value.

In the domain, beside ``harness_options``, because the harness is handed it:
it used to travel as ``options.extra["runtime_profile"]``, a dict every reader
re-checked with ``isinstance`` and whose protocol it compared as a string.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.agent.domain.runtime_profiles import (
    MODEL_PROVIDER_PROTOCOLS,
    HarnessRuntimeConfig,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    describe_invalid,
)
from app.modules.agent.domain.value_objects import JsonObject


class RuntimeSnapshot(BaseModel):
    """What a run needs to rebuild its model client, unredacted. Internal.

    It carries the provider's full config, custom headers included, so it is
    never returned from an API or logged; the listing's projection is
    ``AgentRuntimeProfile.public_dict``. Named for what it is: it used to be
    ``public_snapshot``, an invitation to do exactly that.
    """

    model_config = ConfigDict(frozen=True)

    profile_id: str
    profile_name: str
    user_id: str | None = None
    harness_id: str | None = None
    scope: RuntimeProfileScope
    protocol: RuntimeProfileProtocol
    model_name: str | None = None
    provider_model_name: str | None = None
    model_metadata: JsonObject = Field(default_factory=dict)
    model_capabilities: list[str] = Field(default_factory=list)
    config: JsonObject = Field(default_factory=dict, repr=False)
    #: An Agent Host run's own selections, by option id, laid over the
    #: profile's ``config.config_selections`` (``agent_host_run_config``).
    selection_overrides: dict[str, str] = Field(default_factory=dict)

    @property
    def is_model_provider(self) -> bool:
        """Whether this run's model is called from here, and metered here."""
        return self.protocol in MODEL_PROVIDER_PROTOCOLS

    def harness_config(self) -> HarnessRuntimeConfig:
        """The Agent Host settings, or ``ValueError`` naming what is missing.

        The profile validated this when it was read; a snapshot that fails here
        was built by hand, and dispatching it would send the host a revision
        of nothing.
        """
        try:
            return HarnessRuntimeConfig.model_validate(self.config)
        except ValidationError as error:
            raise ValueError(describe_invalid(error, prefix="config")) from None

    def harness_uuid(self) -> UUID:
        if self.harness_id is None:
            raise ValueError("This runtime is not bound to an Agent Host harness")
        return UUID(self.harness_id)

    def as_payload(self) -> dict[str, object | None]:
        """The dict form usage metering still takes, across its module boundary."""
        return self.model_dump(mode="json")


__all__ = ["RuntimeSnapshot"]
