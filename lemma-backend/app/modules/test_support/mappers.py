"""Make the SQLAlchemy mapper graph complete for tests that compile SQL.

Compiling a statement configures the mappers, and configuration resolves
relationship targets by name — so a test that imports one model module and then
compiles gets ``expression 'Pod' failed to locate a name``. Whether it happens
depends on what else the session imported first, which makes it a test that
passes in a suite and fails on its own.

``migrations/env.py`` solves the same problem the same way: import every model
module for its registration side effect. This wraps that in a call so the
intent is a function invocation rather than a row of imports that look unused
to anything reading the file.
"""

from __future__ import annotations

from sqlalchemy.orm import configure_mappers


def configure_test_mappers() -> None:
    """Register every model, then configure. Idempotent."""
    # Imported inside the function: these are for their registration side
    # effect, and at module scope they read as dead imports.
    from app.core.infrastructure.events import models as _events  # noqa: F401
    from app.modules.agent.infrastructure import models as _agent  # noqa: F401
    from app.modules.agent.infrastructure import (  # noqa: F401
        runtime_models as _agent_runtime,
    )
    from app.modules.apps.infrastructure import models as _apps  # noqa: F401
    from app.modules.connectors.infrastructure import models as _connectors  # noqa: F401
    from app.modules.datastore.infrastructure import models as _datastore  # noqa: F401
    from app.modules.function.infrastructure import models as _function  # noqa: F401
    from app.modules.identity.infrastructure import models as _identity  # noqa: F401
    from app.modules.pod.infrastructure import models as _pod  # noqa: F401
    from app.modules.schedule.infrastructure import models as _schedule  # noqa: F401
    from app.modules.workflow.infrastructure import models as _workflow  # noqa: F401

    configure_mappers()
