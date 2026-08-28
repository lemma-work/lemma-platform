"""Tests for the swallowed-error gate (scripts/check_swallowed_errors.py).

Weighted deliberately towards the negative space. This gate's whole risk is
crying wolf: the codebase is full of broad handlers that *do* report — they
re-raise, they record a ``DependencyIncident``, they return the exception to
the caller — and a gate that flags those gets baselined into irrelevance, which
is worse than no gate. So most of what follows is about what it must stay quiet
about.

The positive cases are the shape of the incident it exists for: a broad handler
around awaited work whose only trace is a ``logger.debug`` that production, at
INFO, never emits.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_checker():
    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "check_swallowed_errors.py"
    )
    spec = importlib.util.spec_from_file_location("check_swallowed_errors", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the script uses `from __future__ import
    # annotations`, so @dataclass resolves field types through sys.modules at
    # class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(source: str, *, path: str = "sample.py") -> list:
    module = _load_checker()
    checker = module.SwallowedErrorChecker(path)
    checker.visit(ast.parse(source))
    return checker.violations


def _rules(source: str) -> list[str]:
    return [violation.rule for violation in _run(source)]


# --- must fire -----------------------------------------------------------


def test_a_broad_catch_that_logs_nothing_is_a_violation() -> None:
    assert _rules(
        "async def f():\n"
        "    try:\n"
        "        await lookup()\n"
        "    except Exception:\n"
        "        return None\n"
    ) == ["silent-broad-catch"]


def test_a_debug_record_without_exc_info_is_a_violation() -> None:
    """The exact shape of the conversation-title bug."""
    assert _rules(
        "async def f():\n"
        "    try:\n"
        "        await generate()\n"
        "    except Exception:\n"
        '        logger.debug("agent.title.thing.diagnostic")\n'
    ) == ["debug-only-broad-catch"]


def test_the_event_name_is_the_detail_so_a_rename_shrinks_the_baseline() -> None:
    violation = _run(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception:\n"
        '        logger.debug("a.b.diagnostic")\n'
    )[0]
    assert violation.detail == "a.b.diagnostic"


@pytest.mark.parametrize("caught", ["BaseException", ""])
def test_swallowing_basexception_without_naming_cancellation_is_a_violation(
    caught: str,
) -> None:
    clause = f"except {caught}:" if caught else "except:"
    assert _rules(
        f"async def f():\n    try:\n        await go()\n    {clause}\n        pass\n"
    ) == ["cancellation-blind-catch"]


def test_a_bare_baseexception_is_flagged_even_around_pure_computation() -> None:
    """`except BaseException: pass` is wrong whatever it guards."""
    assert _rules(
        "def f():\n    try:\n        compute()\n    except BaseException:\n        pass\n"
    ) == ["cancellation-blind-catch"]


def test_pep758_unparenthesised_tuple_is_recognised_as_broad() -> None:
    """Python 3.14 parses `except A, B:` to the same Tuple node."""
    assert _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except ValueError, Exception:\n"
        "        return None\n"
    ) == ["silent-broad-catch"]


def test_two_violations_in_one_function_are_both_reported() -> None:
    """The counted ratchet needs both, or the second rides in free."""
    assert (
        len(
            _run(
                "async def f():\n"
                "    try:\n"
                "        await one()\n"
                "    except Exception:\n"
                "        pass\n"
                "    try:\n"
                "        await two()\n"
                "    except Exception:\n"
                "        pass\n"
            )
        )
        == 2
    )


# --- must stay quiet -----------------------------------------------------


def test_a_handler_that_reraises_is_not_swallowing() -> None:
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception:\n"
        '        logger.debug("a.b.diagnostic")\n'
        "        raise\n"
    )


@pytest.mark.parametrize("value", ["True", "exc"])
def test_exc_info_at_any_level_and_any_value_reports(value: str) -> None:
    """The tree uses both `exc_info=True` and `exc_info=<the exception>`."""
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception as exc:\n"
        f'        logger.debug("a.b.diagnostic", exc_info={value})\n'
    )


@pytest.mark.parametrize("level", ["info", "warning", "error"])
def test_logging_above_debug_is_visible_in_production(level: str) -> None:
    """INFO is the production floor, so these records actually get emitted."""
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception:\n"
        f'        logger.{level}("a.b.degraded")\n'
    )


def test_recording_a_dependency_incident_reports() -> None:
    """The bounded instrument for hot paths must not be flagged as a swallow."""
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await cache.get()\n"
        "    except Exception as exc:\n"
        "        incident.record_failure(error_type=type(exc).__name__)\n"
        "        return None\n"
    )


def test_returning_the_exception_to_the_caller_reports() -> None:
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception as exc:\n"
        "        return Failure(exc)\n"
    )


def test_a_narrow_exception_type_is_a_control_decision() -> None:
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except DatastoreObjectNotFoundError:\n"
        "        return None\n"
    )


def test_a_broad_catch_around_pure_computation_is_out_of_scope() -> None:
    """A coercion guard is a different animal from an outage going silent."""
    assert not _rules(
        "def f():\n    try:\n        int(x)\n    except Exception:\n        return None\n"
    )


def test_the_cancellation_split_is_the_shape_the_gate_wants() -> None:
    """What app/app.py does, and what items 8 and 13 were changed to."""
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await task\n"
        "    except asyncio.CancelledError:\n"
        "        pass\n"
        "    except BaseException:\n"
        '        logger.error("a.b.failed", exc_info=True)\n'
    )


# --- structure -----------------------------------------------------------


def test_a_raise_inside_a_nested_function_does_not_exempt_the_handler() -> None:
    """That `raise` runs when the closure is called, not when the handler is."""
    assert _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception:\n"
        "        def later():\n"
        "            raise RuntimeError()\n"
        "        return later\n"
    ) == ["silent-broad-catch"]


def test_a_log_call_inside_a_nested_function_does_not_exempt_the_handler() -> None:
    assert _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception:\n"
        "        def later():\n"
        '            logger.error("a.b.failed", exc_info=True)\n'
        "        return later\n"
    ) == ["silent-broad-catch"]


@pytest.mark.parametrize(
    "receiver", ["logger", "self._logger", "fs_logger", "self.logger"]
)
def test_the_loggers_this_codebase_actually_uses_are_recognised(receiver: str) -> None:
    assert not _rules(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception:\n"
        f'        {receiver}.warning("a.b.degraded")\n'
    )


def test_the_baseline_key_carries_no_line_number() -> None:
    """So an edit above a violation does not churn the baseline file."""
    violation = _run(
        "async def f():\n"
        "    try:\n"
        "        await go()\n"
        "    except Exception:\n"
        "        return None\n"
    )[0]
    assert "::" in violation.key()
    assert str(violation.line) not in violation.key().split("::")[-1]
    assert violation.key() == "sample.py::f::silent-broad-catch::Exception -> return"


def test_the_baseline_loader_accepts_the_legacy_list_form(tmp_path: Path) -> None:
    module = _load_checker()
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"violations": ["a::b::c::d", "a::b::c::d"]}))
    assert module._load_baseline(path) == {"a::b::c::d": 2}


def test_update_baseline_writes_exactly_two_top_level_keys(tmp_path: Path) -> None:
    module = _load_checker()
    path = tmp_path / "baseline.json"
    argv = sys.argv
    sys.argv = ["check", "--baseline", str(path), "--update-baseline"]
    try:
        assert module.main() == 0
    finally:
        sys.argv = argv
    assert set(json.loads(path.read_text())) == {"_comment", "violations"}
