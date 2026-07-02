"""The single ordered list of open-source modules — the ``INSTALLED_APPS`` of
lemma-backend.

This is the *only* central registration that remains. Each entry references a
module's declarative :class:`~app.core.registry.contract.LemmaModule`. The order
here is canonical: it sets inter-module router order and lifespan entry order.

``lemma-cloud`` does not edit this list; it builds its own
``CLOUD_MODULES = [*OSS_MODULES, ...]`` and feeds that to the same assembly
helpers.
"""

from __future__ import annotations

from app.modules.agent.module import module as agent_module
from app.modules.agent_surfaces.module import module as agent_surfaces_module
from app.modules.datastore.module import module as datastore_module
from app.modules.apps.module import module as app_module
from app.modules.function.module import module as function_module
from app.modules.icon.module import module as icon_module
from app.modules.identity.module import module as identity_module
from app.modules.connectors.module import module as connector_module
from app.modules.pod.module import module as pod_module
from app.modules.pod_bundle.module import module as pod_bundle_module
from app.modules.schedule.module import module as schedule_module
from app.modules.usage.module import module as usage_module
from app.modules.workflow.module import module as workflow_module
from app.modules.workspace.module import module as workspace_module
from app.core.registry.contract import LemmaModule

# Order mirrors the legacy ``app/app.py`` router includes (grouped by module at
# each module's first appearance) so the OpenAPI route set is unchanged.
OSS_MODULES: tuple[LemmaModule, ...] = (
    identity_module,
    pod_module,
    pod_bundle_module,
    datastore_module,
    schedule_module,
    connector_module,
    agent_module,
    function_module,
    app_module,
    workflow_module,
    agent_surfaces_module,
    icon_module,
    usage_module,
    workspace_module,
)
