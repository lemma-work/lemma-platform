"""``python -m app.worker``, with the two outside parties the work tests stand in for.

* the function sandbox: endpoint acquisition answers with the loopback runtime
  at ``E2E_FAKE_FUNCTION_RUNTIME_URL`` (``function_work_support.py``);
* the decision provider: a question is answered ``E2E_DECISION_ANSWER``.

Both are bound before the worker's modules are imported, because the work
kinds capture them at registration. Run as a module so every lane starts as in
production.
"""

import asyncio
import os

from app.modules.decisions.contracts import (
    Answer,
    DecisionCaller,
    DecisionRequest,
    DecisionResult,
)
from app.modules.decisions.contracts import decide as _decide


class _FixedAnswer:
    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult:
        del request, caller
        return DecisionResult(
            answers={"answer": Answer(os.environ.get("E2E_DECISION_ANSWER", "yes"))},
            provider="e2e",
            model="fixed",
        )


_decide.decision_maker = lambda: _FixedAnswer()  # type: ignore[assignment]

import app.events  # noqa: E402,F401  every module's kinds, as app.worker imports them
from app.core.infrastructure.jobs.streaq_runtime import run_worker_lanes  # noqa: E402
from app.modules.function.api import dependencies  # noqa: E402
from app.modules.function.config import function_settings  # noqa: E402
from app.modules.function.tests.e2e.function_work_support import (  # noqa: E402
    loopback_dispatcher_factory,
)

dependencies.build_function_dispatcher = loopback_dispatcher_factory(  # type: ignore[assignment]
    os.environ["E2E_FAKE_FUNCTION_RUNTIME_URL"],
    cap=function_settings.function_runtime_max_concurrent_runs_per_pod,
)


if __name__ == "__main__":
    asyncio.run(run_worker_lanes())
