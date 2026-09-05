"""Schema creation must not depend on which test files a shard collects."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def test_cold_e2e_schema_includes_usage_allocations() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.modules.test_support.e2e_base import _import_e2e_models; "
            "from app.core.infrastructure.db.base import Base; "
            "_import_e2e_models(); "
            "assert {'usage_allocations', 'usage_records', 'usage_limit_counters'} "
            "<= Base.metadata.tables.keys(), 'Usage schema is incomplete'",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
