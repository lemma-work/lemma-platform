"""A generic capability that pairs a toolset with its usage-instructions fragment.

The LEMMA harness contributes per-toolset prompt guidance through capabilities
(``get_instructions``). Web search and todo have bespoke capability classes; this
generic one carries the fragment for any other visible toolset (workspace CLI,
skills, …) so the in-process and remote paths stay in sync — both ultimately read
the same fragment files via the loaders in ``domain.prompts``.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset


class InstructedToolsetCapability[DepsT](AbstractCapability[DepsT]):
    """Expose a toolset and append its usage-instructions fragment."""

    def __init__(
        self,
        toolset: AbstractToolset[DepsT],
        *,
        name: str,
        instructions_loader: Callable[[], str],
    ) -> None:
        self._toolset = toolset
        self._name = name
        self._instructions_loader = instructions_loader

    @property
    def name(self) -> str:
        return self._name

    @classmethod
    def get_serialization_name(cls) -> str | None:
        # Toolset objects and prompt loaders cannot be reconstructed from a spec.
        return None

    def get_toolset(self) -> AbstractToolset[DepsT]:
        return self._toolset

    def get_instructions(self) -> str:
        return self._instructions_loader()
