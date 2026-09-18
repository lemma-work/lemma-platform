"""What one run may spend before it stops and asks whether to carry on.

The cost of agent runs is a tail, not an average: most finish in well under a
minute, and a small handful that never converge account for most of the wall
time spent. Nothing in the system would have ended one of those earlier, because
the only limit that existed was a model-request cap set high enough that a
runaway reached its own natural end first.

Three dimensions, because they catch different runaways and no one of them
catches the others:

* **Model requests** — the loop that will not converge. The cap that existed.
* **Wall clock** — the run that waits. Every call is cheap and the hours are
  not, so a request count never trips.
* **Consecutive tool failures** — the loop that is failing rather than working.
  A run retrying the same broken write is spending exactly as much as one making
  progress, and only the outcome tells them apart.

Deliberately not a token or spend limit here: usage already has one, applied per
organization, and a second ceiling in a second place is two answers to "why did
this stop".

Pure, and separate from the enforcement, so the rules can be read and tested
without a model, a run or a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BudgetDimension(str, Enum):
    MODEL_REQUESTS = "MODEL_REQUESTS"
    WALL_CLOCK = "WALL_CLOCK"
    TOOL_FAILURES = "TOOL_FAILURES"


#: What the person is told, per dimension. Written for somebody who did not
#: watch the run: it says what was spent and what the choice is, not what the
#: counter was called.
_EXHAUSTED_REASONS = {
    BudgetDimension.MODEL_REQUESTS: (
        "This has taken {spent} steps without finishing, which usually means it "
        "is going round in circles rather than closing in."
    ),
    BudgetDimension.WALL_CLOCK: (
        "This has been running for {spent} minutes without finishing."
    ),
    BudgetDimension.TOOL_FAILURES: (
        "The last {spent} actions in a row failed, so it is repeating something "
        "that is not working."
    ),
}


@dataclass(frozen=True, slots=True)
class RunBudget:
    """The ceilings for one run. Zero or below disables that dimension."""

    model_requests: int
    wall_clock_seconds: float
    consecutive_tool_failures: int


@dataclass(frozen=True, slots=True)
class BudgetExhausted:
    """Which ceiling was reached, and how to say so to a person."""

    dimension: BudgetDimension
    spent: int

    @property
    def reason(self) -> str:
        return _EXHAUSTED_REASONS[self.dimension].format(spent=self.spent)


@dataclass(slots=True)
class RunSpend:
    """What a run has spent so far. Counted as it goes, so not frozen.

    There is deliberately no "extend" here. Answering the pause starts a *new*
    run, which builds its own `RunSpend` and therefore its own whole allowance —
    so carrying on needs no bookkeeping, and a counter that only a test ever
    incremented would be worse than none.
    """

    budget: RunBudget
    model_requests: int = 0
    consecutive_tool_failures: int = 0

    def record_model_request(self) -> None:
        self.model_requests += 1

    def record_tool_outcome(self, *, failed: bool) -> None:
        """A success anywhere clears the streak.

        Consecutive, not total: a long run doing real work fails a tool now and
        then — a flaky fetch, a command with a typo — and stopping it for that
        would punish exactly the runs that are converging.
        """
        if failed:
            self.consecutive_tool_failures += 1
        else:
            self.consecutive_tool_failures = 0

    def exhausted(self, *, elapsed_seconds: float) -> BudgetExhausted | None:
        """The first ceiling reached, or None while there is room left."""
        budget = self.budget
        if budget.model_requests > 0 and self.model_requests >= budget.model_requests:
            return BudgetExhausted(BudgetDimension.MODEL_REQUESTS, self.model_requests)
        if (
            budget.wall_clock_seconds > 0
            and elapsed_seconds >= budget.wall_clock_seconds
        ):
            return BudgetExhausted(
                BudgetDimension.WALL_CLOCK, int(elapsed_seconds // 60)
            )
        if (
            budget.consecutive_tool_failures > 0
            and self.consecutive_tool_failures >= budget.consecutive_tool_failures
        ):
            return BudgetExhausted(
                BudgetDimension.TOOL_FAILURES, self.consecutive_tool_failures
            )
        return None
