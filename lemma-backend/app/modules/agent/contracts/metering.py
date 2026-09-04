"""Model work another module does on the deployment's credentials.

A module outside `agent` that wants a model -- `pod_bundle` polishing a README,
`schedule` evaluating a filter -- is doing the same thing an agent run does, on
the same credentials, and owes the same accounting. `billed` is the scope that
makes that accounting a property of asking for the model rather than of
remembering to write it down afterwards.

Published beside `model_runtime`, and for the same reason: that contract hands a
caller the system model, and this one is what makes running it billable. A
caller needs both, and neither is a reach into `agent/services/`.

A submodule rather than an addition to `contracts/__init__`, which is a leaf:
everything importing any agent contract would otherwise pay for pydantic-ai's
wrapper machinery.
"""

from __future__ import annotations

from app.modules.agent.services.metered_model import MeteringScope, billed

__all__ = ["MeteringScope", "billed"]
