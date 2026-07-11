"""Invariant: enum value casing convention.

Internal-domain string enums (state / kind / type / role of our own making) use
UPPERCASE values, matching the rest of the codebase. A small, documented
allow-list stays lowercase because the value is an EXTERNAL contract — changing
it would break correctness, not just style:

  * ``MessageRole`` — the LLM-ecosystem convention (OpenAI/Anthropic/pydantic-ai
    all use ``user``/``assistant``/``system``/``tool``).
  * REST/SQL query operators (``eq``/``gt``/``like``/``asc``/``desc``).
  * Vendor / external-provider identifiers (search engines, speech provider).

This test locks both halves so a future change can't silently regress either:
new internal enums won't drift to lowercase, and the wire-contract enums won't
get "consistency-flipped" to uppercase.
"""

from __future__ import annotations

from app.core.authorization.delegation import WorkloadPrincipalType
from app.core.web_search.search_client import AvailableSearchEngines
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.tools.file_entities import FileType as AgentFileType
from app.modules.agent.tools.speech.provider import SpeechProviderName
from app.modules.agent_surfaces.domain.entities import (
    ConversationType as SurfaceConversationType,
)
from app.modules.datastore.api.schemas.datastore_schemas import (
    RecordFilterOperator,
    RecordSortDirection,
)
from app.modules.datastore.domain.events import DatastoreRecordOperation
from app.modules.usage.domain.entities import UsageKind
from app.modules.workspace.domain.file_types import FileType as WorkspaceFileType

# Internal-domain enums — values MUST be UPPERCASE.
_INTERNAL_CAPS_ENUMS = [
    MessageKind,
    UsageKind,
    SurfaceConversationType,
    WorkloadPrincipalType,
    DatastoreRecordOperation,
    WorkspaceFileType,
    AgentFileType,
]

# External / wire-contract enums — values MUST stay lowercase (with the reason).
_LOWERCASE_ALLOW_LIST = [
    MessageRole,  # LLM message-role wire convention
    RecordFilterOperator,  # REST/SQL query operator convention
    RecordSortDirection,  # REST/SQL sort-direction convention
    AvailableSearchEngines,  # external search-engine identifiers
    SpeechProviderName,  # external speech-provider identifier
]


def test_internal_domain_enums_use_uppercase_values():
    offenders: list[str] = []
    for enum_cls in _INTERNAL_CAPS_ENUMS:
        for member in enum_cls:
            if member.value != member.value.upper():
                offenders.append(f"{enum_cls.__name__}.{member.name} = {member.value!r}")
    assert not offenders, (
        "Internal-domain enums must use UPPERCASE values. Lowercase offenders: "
        + ", ".join(offenders)
    )


def test_wire_contract_enums_stay_lowercase():
    offenders: list[str] = []
    for enum_cls in _LOWERCASE_ALLOW_LIST:
        for member in enum_cls:
            if member.value != member.value.lower():
                offenders.append(f"{enum_cls.__name__}.{member.name} = {member.value!r}")
    assert not offenders, (
        "These enums are external/wire contracts and must stay lowercase "
        "(changing them breaks integrations, not just style): "
        + ", ".join(offenders)
    )
