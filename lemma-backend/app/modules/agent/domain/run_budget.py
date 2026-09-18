"""What one run may spend before it stops and asks whether to carry on.

These are backstops for a run that has stopped making progress, not a schedule
for one that is working. Long work is wanted here: a run that spends an hour and
returns the thing asked for is a success, and nothing below should hurry it. The
ceilings sit far enough out that ordinary work -- including slow, patient,
genuinely long work -- never reaches them, and only a run going round in circles
does.

The cost of agent runs is a tail, not an average: most finish quickly, and a
small handful that never converge account for most of the wall time spent.
Nothing in the system would have ended one of those earlier, because the only
limit that existed was a model-request cap set high enough that a runaway
reached its own natural end first.

A run is told before it arrives. Hitting a ceiling unannounced turns a budget
into a trap -- the run is interrupted mid-thought with no chance to land what it
has -- so each dimension warns first, once, and the run gets to choose what to
do with the room that is left.

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

from dataclasses import dataclass, field
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


#: What the *model* is told as it nears a ceiling, per dimension. Addressed to
#: the run rather than to a person: it says how much room is left and what to do
#: with it, because the point of warning early is to let the run land what it
#: has rather than be cut off holding it.
_APPROACHING_NOTICES = {
    BudgetDimension.MODEL_REQUESTS: (
        "You have used about {spent} of roughly {limit} steps for this run. "
        "There is room left -- keep going if the work needs it -- but if you "
        "are not close, start landing what you have: record the useful parts "
        "and say plainly what remains."
    ),
    BudgetDimension.WALL_CLOCK: (
        "This run has been going about {spent} minutes of roughly {limit}. "
        "There is time left -- keep going if the work needs it -- but if you "
        "are not close, start landing what you have rather than beginning "
        "something you cannot finish."
    ),
    BudgetDimension.TOOL_FAILURES: (
        "{spent} actions in a row have now failed, and the run stops to ask at "
        "{limit}. Whatever is being retried is not working: change approach, or "
        "report what is blocking you, rather than repeating it."
    ),
}


@dataclass(frozen=True, slots=True)
class BudgetWarning:
    """A ceiling coming into view, and what to tell the run about it."""

    dimension: BudgetDimension
    spent: int
    limit: int

    @property
    def notice(self) -> str:
        return _APPROACHING_NOTICES[self.dimension].format(
            spent=self.spent, limit=self.limit
        )


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
    #: Notices waiting to be handed to the model, oldest first. Filled here and
    #: drained by whoever is next to build a request, so the decision that a
    #: warning is due stays with the thing that counts the spending.
    notices: list[str] = field(default_factory=list)
    #: Dimensions already warned about. A warning repeated every step would be
    #: nagging rather than news, and the run cannot act on it twice.
    warned: set[BudgetDimension] = field(default_factory=set)

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

    def approaching(self, *, elapsed_seconds: float, at: float) -> BudgetWarning | None:
        """The first ceiling this run is within `at` of, warned about once.

        Queues the notice as a side effect, because "has been warned" and "the
        warning still needs delivering" are the same fact and splitting them
        across two objects is how one of them goes stale. `at` outside (0, 1)
        switches warning off without touching the ceilings themselves.
        """
        if not 0 < at < 1:
            return None
        budget = self.budget
        # Measured raw and reported rounded: minutes read better than seconds in
        # a notice, but a threshold compared in minutes would fire a minute late.
        dimensions = (
            (
                BudgetDimension.MODEL_REQUESTS,
                float(self.model_requests),
                float(budget.model_requests),
                self.model_requests,
                budget.model_requests,
            ),
            (
                BudgetDimension.WALL_CLOCK,
                elapsed_seconds,
                budget.wall_clock_seconds,
                int(elapsed_seconds // 60),
                int(budget.wall_clock_seconds // 60),
            ),
            (
                BudgetDimension.TOOL_FAILURES,
                float(self.consecutive_tool_failures),
                float(budget.consecutive_tool_failures),
                self.consecutive_tool_failures,
                budget.consecutive_tool_failures,
            ),
        )
        for dimension, spent, limit, shown_spent, shown_limit in dimensions:
            if limit <= 0 or dimension in self.warned:
                continue
            if spent < limit * at:
                continue
            self.warned.add(dimension)
            warning = BudgetWarning(dimension, spent=shown_spent, limit=shown_limit)
            self.notices.append(warning.notice)
            return warning
        return None

    def take_notices(self) -> list[str]:
        """Hand over the queued notices and forget them."""
        pending, self.notices = self.notices, []
        return pending

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
