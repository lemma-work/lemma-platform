"""Smoke test for the module registry.

Force-invokes every thunk each installed module declares so that a broken
import in any ``app/modules/<name>/module.py`` (or the controllers/handlers it
pulls) fails fast in CI, instead of only when the full app is assembled. The
thunks are lazy by design, so nothing else exercises them at import time.
"""

from __future__ import annotations

import pytest

from app.core.registry.installed import OSS_MODULES

pytestmark = pytest.mark.unit


def test_oss_module_names_are_unique() -> None:
    names = [m.name for m in OSS_MODULES]
    assert len(names) == len(set(names)), f"duplicate module names: {names}"


def test_oss_module_router_thunks_import() -> None:
    for module in OSS_MODULES:
        if module.routers is None:
            continue
        routers = module.routers()
        assert routers, f"{module.name}.routers() returned nothing"
        for router in routers:
            assert hasattr(router, "routes"), (
                f"{module.name} contributed a non-APIRouter: {router!r}"
            )


def test_oss_module_event_and_streaq_thunks_import() -> None:
    # event_routers / register_streaq are added in Phase 3; this stays correct
    # before then (the guards simply skip) and starts covering them after.
    for module in OSS_MODULES:
        if module.register_streaq is not None:
            module.register_streaq()
        if module.event_routers is not None:
            for router in module.event_routers():
                assert (
                    hasattr(router, "subscriber")
                    or hasattr(router, "_handlers")
                    or router is not None
                )
