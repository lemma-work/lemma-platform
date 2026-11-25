"""Reports module registration: no routes of its own -- a report is filed
from the message it is about -- and the work that emails it."""

from app.core.registry import LemmaModule


def _register_work() -> None:
    import app.modules.reports.events.report_email  # noqa: F401


module = LemmaModule(name="reports", register_work=_register_work)
