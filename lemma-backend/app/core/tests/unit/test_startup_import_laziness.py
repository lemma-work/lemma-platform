"""The heavy libraries an API process must not import to serve a health check.

Serving HTTP does not require the connector SDK. It used to import it anyway:
one module-scope line in ``composio_auth_provider`` pulled ``composio``, which
pulls its default OpenAI provider, so every backend start loaded 993 modules and
about a second of work it had no use for. On Lemma Desktop that lands on the
cold start a person waits through before the window opens.

Nothing structural stops it coming back. The import is one convenience line away
at any time, in a module nobody is thinking about while adding a type hint, and
the symptom -- a second of startup -- is invisible in review and in every test
that only asserts behaviour.

``check_import_budget.py`` counts total modules and would catch a regression of
this size, but it reports a number, not a culprit: "5,578 modules, baseline
4,585" does not say *what* arrived. These tests name the library, so the failure
tells you which import to look at.

Measured in a subprocess because ``app.app`` is already imported by the time any
test runs, and purging a package that large from ``sys.modules`` mid-process is
not a faithful reproduction of a fresh interpreter. Same technique as the budget
gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]

_PROBE = """
import importlib, json, sys
importlib.import_module("app.app")
print(json.dumps(sorted({name.split(".")[0] for name in sys.modules})))
"""


def _roots_loaded_by_app() -> set[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert completed.returncode == 0, (
        f"importing app.app failed:\n{completed.stderr[-2000:]}"
    )
    return set(json.loads(completed.stdout.splitlines()[-1]))


# Each entry: the library, and where it came from when it was last let in.
_MUST_STAY_LAZY = {
    "composio": (
        "the connector SDK, reached from app.app through the connector router. "
        "Import it inside the method that needs it, keeping the "
        "COMPOSIO_CACHE_DIR setdefault above the import -- the SDK reads that "
        "at import time"
    ),
    "openai": (
        "not imported directly by us at all: it arrives inside composio, which "
        "imports its default provider. If composio is lazy this is too"
    ),
}


@pytest.mark.parametrize("library", sorted(_MUST_STAY_LAZY))
def test_serving_the_api_does_not_import(library: str) -> None:
    loaded = _roots_loaded_by_app()
    assert library not in loaded, (
        f"importing app.app loaded {library!r}, which serving a request does "
        f"not need. {_MUST_STAY_LAZY[library]}. Find the import with:\n"
        f"    uv run python -X importtime -c 'import app.app'\n"
        f"and walk the parent chain -- the library in the profile is rarely the "
        f"one that imported it."
    )
