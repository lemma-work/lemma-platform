"""Ratchet keeping the removed schedule/workflow runtime shims from returning.

`scripts/check_architecture.py` guards module boundaries; it cannot see a file
coming back from a stale branch or a bad merge resolution. This test names the
exact paths and symbols that were deleted so reintroducing one fails here with
an obvious message instead of silently restoring a second runtime path.

This file necessarily *mentions* every forbidden symbol, so it excludes itself
from the scan rather than obfuscating the strings.
"""

import re
from pathlib import Path


# `app/`, five levels up -- not four. It was `parents[3]`, which is `app/modules`,
# so every `composition/...` entry below named a path that could never exist and
# the source scan never opened a file outside `app/modules` at all. The two
# halves of this ratchet were both reading a tree that did not contain the thing
# they forbid.
APP_ROOT = Path(__file__).resolve().parents[4]

# Deleted modules. Each had a live replacement at the time of removal: the
# workflow adapters were re-export shims over app/composition, the filter job
# service was already unreachable, and the schedule composition files below
# were emptied into the modules that own what they were building.
#
# `modules/schedule/infrastructure/adapters/external_schedule_writer.py` used to
# be on this list, and is not any more: it was removed because a *second*
# runtime path existed alongside the one in the composition root, and it is back
# because that root file is now the one that is gone. What this ratchet forbids
# is two paths, not this path.
REMOVED_PATHS = (
    "modules/schedule/infrastructure/adapters/datastore_adapter.py",
    "modules/schedule/infrastructure/schedule_managers/composio.py",
    "modules/schedule/infrastructure/schedule_managers/manager_factory.py",
    "modules/schedule/services/schedule_filter_job_service.py",
    "modules/workflow/infrastructure/adapters/agent_adapter.py",
    "modules/workflow/infrastructure/adapters/function_adapter.py",
    "modules/workflow/infrastructure/adapters/schedule_adapter.py",
    "composition/schedule_connectors.py",
    "composition/schedule_run_recovery.py",
    "composition/workflow_agent.py",
    # The filter is schedule's own adapter now
    # (`infrastructure/adapters/system_model_filter.py`) and the README half is
    # `agent/contracts/model_runtime.py`. Both were re-exports that made agent
    # service module paths part of another module's build.
    "composition/schedule_filter.py",
    "composition/pod_bundle_readme.py",
)

# Import paths for the same modules, plus the sentinel that used to stand in for
# a missing schedule-run owner. Every schedule run now has a real user_id, so
# the sentinel returning would mean the ownership invariant had been reopened.
FORBIDDEN_REFERENCES = (
    "_LEGACY_MISSING_USER_ID",
    "modules.schedule.infrastructure.adapters.datastore_adapter",
    "modules.schedule.infrastructure.schedule_managers.composio",
    "modules.schedule.infrastructure.schedule_managers.manager_factory",
    "modules.workflow.infrastructure.adapters",
    "composition.schedule_connectors",
    "composition.schedule_run_recovery",
    "composition.workflow_agent",
    "composition.schedule_filter",
    "composition.pod_bundle_readme",
)


# Adjacent string literals, which Python concatenates at compile time. A
# reference split across two of them reads as one dotted path to the
# interpreter and as two harmless fragments to a substring scan — which is
# exactly how a stale `monkeypatch.setattr` target for a deleted module
# survived this ratchet until a real-execution e2e run tripped over it.
_LITERAL_SEAM = re.compile(r"""["']\s*\n?\s*["']""")


def _joined(source: str) -> str:
    """Source with implicit string concatenation closed up."""
    return _LITERAL_SEAM.sub("", source)


def _scanned_sources() -> list[tuple[Path, str]]:
    this_file = Path(__file__).resolve()
    return [
        (path, _joined(path.read_text()))
        for path in APP_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and path.resolve() != this_file
    ]


def test_removed_schedule_runtime_modules_stay_removed() -> None:
    restored = [path for path in REMOVED_PATHS if (APP_ROOT / path).exists()]
    assert not restored, f"deleted runtime modules are back: {restored}"


def test_nothing_references_the_removed_runtime_or_the_owner_sentinel() -> None:
    offenders = [
        f"{path.relative_to(APP_ROOT)}: {reference}"
        for path, source in _scanned_sources()
        for reference in FORBIDDEN_REFERENCES
        if reference in source
    ]
    assert not offenders, "removed runtime is referenced again:\n" + "\n".join(
        offenders
    )
