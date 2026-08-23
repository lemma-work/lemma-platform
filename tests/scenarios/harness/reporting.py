"""Make a run read as product journeys instead of module paths.

The problem this solves is the one an operator actually has: a CI run that says
``lemma-backend e2e (surfaces)`` and ``test_say_falls_back_to_native_file_on_slack
PASSED`` tells you a module is green. It does not tell you which promise to the
user is being kept.

So after the run this plugin prints the same results grouped the way the
specification is organised:

    Getting started
      Sign up and get a working pod
        ✓  A new person signs up and lands in their own organization   PS-ONB-001   4.2s
        ✗  An invited member sees only the pods they were added to     PS-ONB-003   2.1s

Two deliberate non-choices:

* **Node ids are left alone.** Rewriting ``item._nodeid`` would put the sentence
  into the JUnit XML directly, and it is tempting. It also breaks every tool
  that round-trips a node id — ``--lf``, ``-k``, re-running one failure by
  pasting it back. The tree goes to the terminal; the structured form goes to
  JUnit as properties, below.
* **Nothing here validates.** Whether a ``PS-`` id exists is
  ``scripts/check_scenario_coverage.py``'s job, and it does that by reading the
  source rather than running it. A reporting plugin that could fail a run would
  make the gates depend on the suite executing.

Each test also records ``journey``, ``capability``, ``scenario`` and ``proves``
as JUnit ``<property>`` elements, so a CI front end that reads the XML can show
the sentence without this plugin being involved.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pytest


_OUTCOME_GLYPH = {
    "passed": "✓",
    "failed": "✗",
    "error": "✗",
    "skipped": "–",
    "not run": "·",
}
_OUTCOME_MARKUP = {
    "passed": {"green": True},
    "failed": {"red": True, "bold": True},
    "error": {"red": True, "bold": True},
    "skipped": {"yellow": True},
}


@dataclass
class _Result:
    scenario: str
    proves: tuple[str, ...]
    #: Starts at "not run" rather than "passed" deliberately. A collected but
    #: unexecuted scenario — `--collect-only`, a run cut short, a worker that
    #: died — must never render as a tick. The whole value of this output is
    #: that an operator can trust it at a glance.
    outcome: str = "not run"
    duration: float = 0.0
    nodeid: str = ""


@dataclass
class _Capability:
    name: str
    results: list[_Result] = field(default_factory=list)


def _first_mark_arg(item: pytest.Item, name: str) -> str | None:
    mark = item.get_closest_marker(name)
    if mark is None or not mark.args:
        return None
    return str(mark.args[0])


def _all_mark_args(item: pytest.Item, name: str) -> tuple[str, ...]:
    # iter_markers walks module -> class -> function, so several @proves stack
    # rather than the innermost silently winning.
    return tuple(
        str(arg) for mark in item.iter_markers(name) for arg in mark.args
    )


#: ``pytest_runtest_logreport`` is not handed the config, and the report object
#: does not carry it either. Stashing the active config at configure time is the
#: supported way across pytest versions; there is exactly one per process.
_CONFIG: pytest.Config | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _CONFIG
    _CONFIG = config
    for name, help_text in (
        ("journey", "journey(title): the stretch of product this test belongs to"),
        ("capability", "capability(title): the thing a person can do"),
        ("scenario", "scenario(title): what this test proves, as a sentence"),
        ("proves", "proves(*ids): PS- promises in docs/product this test proves"),
        ("covers", "covers(*names): operation ids and analytics events exercised"),
    ):
        config.addinivalue_line("markers", f"{name}: {help_text}")
    config._scenario_results = {}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Attach the structured identity to each test, for JUnit and for the tree."""
    for item in items:
        journey = _first_mark_arg(item, "journey")
        if journey is None:
            continue
        capability = _first_mark_arg(item, "capability") or "(uncategorised)"
        scenario = _first_mark_arg(item, "scenario") or item.name
        proves = _all_mark_args(item, "proves")

        item.user_properties.append(("journey", journey))
        item.user_properties.append(("capability", capability))
        item.user_properties.append(("scenario", scenario))
        if proves:
            item.user_properties.append(("proves", " ".join(proves)))

        config._scenario_results[item.nodeid] = (
            journey,
            capability,
            _Result(scenario=scenario, proves=proves, nodeid=item.nodeid),
        )


@pytest.hookimpl(trylast=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _CONFIG is None:
        return
    entry = getattr(_CONFIG, "_scenario_results", {}).get(report.nodeid)
    if entry is None:
        return
    _, _, result = entry

    if report.when == "call":
        result.duration = report.duration
        result.outcome = report.outcome
    elif report.when in ("setup", "teardown") and report.failed:
        # A fixture that blows up is the scenario failing, not a separate event.
        result.outcome = "error"
    elif report.when == "setup" and report.skipped:
        result.outcome = "skipped"


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    results = getattr(config, "_scenario_results", {})
    if not results:
        return

    tree: dict[str, dict[str, _Capability]] = defaultdict(dict)
    for journey, capability, result in results.values():
        tree[journey].setdefault(capability, _Capability(capability)).results.append(
            result
        )

    executed = [
        result for _, _, result in results.values() if result.outcome != "not run"
    ]
    if not executed:
        # Collection without execution. Show what would run, claim nothing.
        terminalreporter.write_sep("=", "product scenarios (collected)", bold=True)
        for journey in sorted(tree):
            terminalreporter.write_line("")
            terminalreporter.write_line(journey, bold=True)
            for capability in sorted(tree[journey]):
                terminalreporter.write_line(f"  {capability}")
                for result in tree[journey][capability].results:
                    terminalreporter.write_line(f"    ·  {result.scenario}")
        return

    write = terminalreporter.write_line
    terminalreporter.write_sep("=", "product scenarios", bold=True)

    passed = failed = 0
    for journey in sorted(tree):
        write("")
        write(journey, bold=True)
        for capability in sorted(tree[journey]):
            write(f"  {capability}")
            for result in tree[journey][capability].results:
                if result.outcome == "passed":
                    passed += 1
                elif result.outcome in ("failed", "error"):
                    failed += 1
                glyph = _OUTCOME_GLYPH.get(result.outcome, "?")
                promises = " ".join(result.proves)
                line = f"    {glyph}  {result.scenario}"
                if promises:
                    line = f"{line}  [{promises}]"
                line = f"{line}  {result.duration:.1f}s"
                terminalreporter.write_line(
                    line, **_OUTCOME_MARKUP.get(result.outcome, {})
                )

    write("")
    summary = f"{passed}/{passed + failed} scenarios passing"
    terminalreporter.write_line(summary, bold=True, red=bool(failed), green=not failed)

    _waiting_on_a_person(terminalreporter)


def _waiting_on_a_person(terminalreporter) -> None:
    """What nobody could do for the suite, and what to do about it.

    Its own section rather than a line in the skip list, because a skip reads as
    "not applicable here" and this is "somebody needs to go and click
    something". Left in the skip list it becomes a number that quietly goes down
    — a suite proving less each month and never saying so, which is the same rot
    the stand-ins had by a different route.
    """
    from harness import consent

    outstanding = consent.outstanding()
    if not outstanding:
        return

    write = terminalreporter.write_line
    write("")
    terminalreporter.write_sep("=", "waiting on a person", bold=True, yellow=True)
    for action in outstanding:
        write("")
        write(f"  {action.name}", bold=True)
        write(f"    do    {action.how}")
        write("    then  re-run `make scenarios-provision TARGET=…` to confirm")
    write("")
    write(
        f"{len(outstanding)} thing{'' if len(outstanding) == 1 else 's'} only a "
        f"person can do. Until then the scenarios that need them are not "
        f"running — and not failing either, which is why this says so here.",
        yellow=True,
    )
