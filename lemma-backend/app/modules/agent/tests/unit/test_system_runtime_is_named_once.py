"""The deployment's own runtime has one spelling, and a run must name its own.

A run built without a runtime used to default to the system profile's literal
id, so a creator that forgot to say read as "ran on the system model". Two more
copies of the literal sat in persistence code, beside the constant they should
have used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.domain.runtime_profiles import SYSTEM_LEMMA_PROFILE_ID

pytestmark = pytest.mark.unit

_APP = Path(__file__).resolve().parents[4]
_DEFINED_IN = _APP / "modules" / "agent" / "domain" / "runtime_profiles.py"


def test_a_run_without_a_runtime_is_refused() -> None:
    with pytest.raises(ValidationError, match="agent_runtime"):
        AgentRun(conversation_id=uuid4(), started_at=datetime.now(timezone.utc))


def test_only_the_constant_spells_the_system_profile_id() -> None:
    literal = f'"{SYSTEM_LEMMA_PROFILE_ID}"'
    spelled = [
        path.relative_to(_APP).as_posix()
        for path in _APP.rglob("*.py")
        if "tests" not in path.parts
        and not path.name.startswith("test_")
        and path != _DEFINED_IN
        and literal in path.read_text(encoding="utf-8")
    ]

    assert spelled == []
