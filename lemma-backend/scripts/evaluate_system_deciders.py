"""Run the system deciders' cases against real engines, one rung at a time.

Opt-in, like `lemma-frontend/scripts/check-call-routing.ts`: it calls real
providers and costs real money, so CI never runs it. Each engine is asked on its
own, not through the ladder, and its answers go through the same policy the
ladder applies -- so what is measured is what that rung would contribute in
production, including the answers the policy refuses.

    uv run python scripts/evaluate_system_deciders.py --engine system_one
    uv run python scripts/evaluate_system_deciders.py --engine model --case "a question back"

System One needs `TYPESAFE_API_KEY`. The model rung needs a configured model and
the dev stack's database, because every model call is metered.

Exit status is 1 when any rung let a forbidden answer through its policy: that
is a safety failure whatever the accuracy. Accuracy is printed, not enforced.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.decisions.domain.deciders import Lane  # noqa: E402
from app.modules.decisions.domain.ports import (  # noqa: E402
    Ask,
    Engine,
    EngineFailedError,
    EngineUnavailableError,
    Payer,
)
from app.modules.decisions.infrastructure.model_engine import ModelEngine  # noqa: E402
from app.modules.decisions.infrastructure.typesafe_engine import (  # noqa: E402
    SystemOneEngine,
)
from app.modules.decisions.services.decisions_service import (  # noqa: E402
    asked_questions,
)
from app.modules.decisions.services.ladder import accept  # noqa: E402
from app.modules.decisions.services.rendering import render  # noqa: E402
from app.modules.decisions.services.system_decider_cases import (  # noqa: E402
    SYSTEM_DECIDER_CASES,
    DeciderCase,
)
from app.modules.decisions.services.system_deciders import system_decider  # noqa: E402


async def _run_case(engine: Engine, case: DeciderCase) -> dict[str, object]:
    definition = system_decider(case.decider)
    if definition is None:
        raise SystemExit(f"unknown decider {case.decider}")
    questions = asked_questions(definition, case.options)
    rendered = render(case.state, definition.input)
    ask = Ask(
        questions=questions,
        evidence=rendered.text,
        guidance=definition.guidance,
        examples={},
        lane=Lane.AMBIENT,
        payer=Payer(user_id=None, organization_id=None, pod_id=None),
    )
    started = time.monotonic()
    try:
        outcome = await engine.answer(ask)
    except (EngineUnavailableError, EngineFailedError) as exc:
        return {"case": case.name, "rung": engine.rung.value, "error": str(exc)}
    latency_ms = int((time.monotonic() - started) * 1000)
    standing = accept(outcome, definition.policy, questions, engine.rung)
    raw = {key: answer.value for key, answer in outcome.answers.items()}
    final = {
        key: standing[key].value if key in standing else fallback
        for key, fallback in _fallbacks(dict(questions)).items()
    }
    passed = all(final.get(key) in values for key, values in case.accepted.items())
    unsafe = any(
        key in standing and standing[key].value in values
        for key, values in case.forbidden.items()
    )
    return {
        "case": case.name,
        "rung": engine.rung.value,
        "said": raw,
        "stands": final,
        "confidence": {
            key: answer.confidence for key, answer in outcome.answers.items()
        },
        "passed": passed,
        "unsafe": unsafe,
        "latency_ms": latency_ms,
    }


def _fallbacks(questions: dict[str, object]) -> dict[str, object]:
    """What each question falls back to when no rung commits."""
    return {
        key: getattr(question, "fallback", None) for key, question in questions.items()
    }


async def _main(engine_names: list[str], case_filter: str | None) -> int:
    engines: list[Engine] = []
    if "system_one" in engine_names:
        engines.append(SystemOneEngine())
    if "model" in engine_names:
        engines.append(ModelEngine())
    cases = [
        case
        for case in SYSTEM_DECIDER_CASES
        if case_filter is None or case.name == case_filter
    ]
    unsafe = 0
    for engine in engines:
        if not engine.is_available(organization_id=None):
            print(json.dumps({"rung": engine.rung.value, "error": "not configured"}))
            continue
        passed = 0
        for case in cases:
            row = await _run_case(engine, case)
            print(json.dumps(row, default=str))
            passed += int(bool(row.get("passed")))
            unsafe += int(bool(row.get("unsafe")))
        print(
            json.dumps(
                {"rung": engine.rung.value, "passed": passed, "total": len(cases)}
            )
        )
    return 1 if unsafe else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--engine",
        choices=["system_one", "model", "both"],
        default="both",
        help="Which rung to evaluate.",
    )
    parser.add_argument(
        "--case", default=None, help="Run only the case with this name."
    )
    args = parser.parse_args()
    names = ["system_one", "model"] if args.engine == "both" else [args.engine]
    return asyncio.run(_main(names, args.case))


if __name__ == "__main__":
    raise SystemExit(main())
