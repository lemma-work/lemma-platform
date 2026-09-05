import pytest
from pydantic import ValidationError

from app.modules.function.config import FunctionRevisionSettings


def test_retention_ceiling_cannot_be_lower_than_its_floor(monkeypatch):
    monkeypatch.setenv("FUNCTION_REVISION_KEEP_LAST", "10")
    monkeypatch.setenv("FUNCTION_REVISION_MAX_KEEP", "2")
    with pytest.raises(ValidationError, match="FUNCTION_REVISION_MAX_KEEP"):
        FunctionRevisionSettings()
