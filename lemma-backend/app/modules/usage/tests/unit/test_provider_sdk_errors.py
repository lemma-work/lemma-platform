"""A provider SDK's connection error is recognised without importing the SDK.

`provider_retries` imported ``anthropic`` for one exception class, which loaded
that whole SDK into every process the moment it first metered a model call --
on the event loop of the first turn to do it -- whether or not anything in the
process talks to Anthropic. The class is now looked up among the modules already
loaded, since an SDK's error can only be raised once something imported it.

Both halves are pinned: metering imports no SDK, and an SDK that is loaded has
its drops classified exactly as before.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest

from app.modules.usage.infrastructure.provider_retries import (
    is_harness_owned_drop,
    provider_errors,
)

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[5]

# In a fresh interpreter: by the time any test runs, something in the suite has
# usually imported the SDK already.
_PROBE = """
import json, sys
from app.modules.usage.infrastructure import metered_model, provider_retries
provider_retries.provider_errors()
provider_retries.is_harness_owned_drop(ConnectionResetError())
print(json.dumps("anthropic" in sys.modules))
"""


def test_metering_a_model_call_does_not_load_the_anthropic_sdk() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert json.loads(completed.stdout.splitlines()[-1]) is False, (
        "importing metered_model loaded `anthropic`. Name an SDK's error through "
        "`provider_errors()` rather than importing the SDK to reach it."
    )


@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
def test_a_loaded_sdk_s_connection_drop_is_left_to_the_harness(sdk: str) -> None:
    connection_error = importlib.import_module(sdk).APIConnectionError
    dropped = connection_error(
        request=httpx2.Request("POST", "https://provider.example.test/v1")
    )

    assert isinstance(dropped, provider_errors())
    assert is_harness_owned_drop(dropped)
