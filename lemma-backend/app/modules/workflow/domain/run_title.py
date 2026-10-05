"""What a run is about, in a few words.

A workflow that takes days and passes between people is looked at as a set of
runs in flight, and "Waiting on a person, started 3d ago" four times over does
not say which one is the Priya Shah offer and which is the Acme renewal. The
author names the run once, on the workflow, as up to four expressions over the
run context — `["collect.candidate_name", "collect.role"]` — and every run is
titled by evaluating them against its own context whenever it is read.

Evaluated at read time rather than stamped onto the run: a title whose source
field is filled in by a form three steps in has to change as the run moves, and
a template the author edits should retitle runs already in flight.
"""

from app.modules.workflow.domain.errors import ExpressionSyntaxError
from app.modules.workflow.domain.expressions import ExpressionEngine

MAX_RUN_TITLE_PARTS = 4
MAX_RUN_TITLE_LENGTH = 120
SEPARATOR = " · "


def validate_run_title(expressions: list[str] | None) -> list[str]:
    """Compile every part, so a bad expression is refused on save rather than
    silently titling nothing. Raises ValueError, which a request model turns
    into a 422 naming the expression."""
    if not expressions:
        return []
    if len(expressions) > MAX_RUN_TITLE_PARTS:
        raise ValueError(
            f"run_title takes at most {MAX_RUN_TITLE_PARTS} expressions, "
            f"got {len(expressions)}"
        )
    cleaned: list[str] = []
    for raw in expressions:
        # The stored value is the one compiled, and an empty part is refused
        # here rather than trusting the compiler to; `split_start` would
        # otherwise drop it on read and the saved title would differ.
        expression = raw.strip()
        if not expression:
            raise ValueError("run_title: an expression must not be empty")
        try:
            ExpressionEngine.compile(expression)
        except ExpressionSyntaxError as exc:
            raise ValueError(f"run_title: {exc}") from exc
        cleaned.append(expression)
    return cleaned


def render_run_title(expressions: list[str], view: dict[str, object]) -> str | None:
    """The title for one run, or None when nothing it names is filled in yet.

    A part that resolves to nothing is skipped, not printed as "None": the
    first step of most runs has not happened when the run is first listed, and
    "None · None" is worse than the fallback the client draws without a title.
    Lists and objects are skipped too — a title is a line, not a dump.
    """
    parts: list[str] = []
    for expression in expressions:
        try:
            value = ExpressionEngine.evaluate(expression, view)
        except ExpressionSyntaxError:
            # Validated on save, so only a template stored before validation
            # existed can get here. One bad part costs that part, not the list.
            continue
        said = _say(value)
        if said:
            parts.append(said)
    if not parts:
        return None
    title = SEPARATOR.join(parts)
    if len(title) > MAX_RUN_TITLE_LENGTH:
        title = title[: MAX_RUN_TITLE_LENGTH - 1].rstrip() + "…"
    return title


def _say(value: object) -> str | None:
    if value is None or isinstance(value, (list, dict)):
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    said = str(value).strip()
    return " ".join(said.split()) or None
