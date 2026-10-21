"""Why a run failed, in the sentence its reader can act on.

The runner already writes that sentence for the web transcript -- a quota
exhausted, a provider down, a local model server not running. A surface (Slack,
Telegram, email) is told about the same failure through its run observer, which
receives only the exception, so the sentence is published here rather than
rebuilt there.
"""

from __future__ import annotations

from functools import lru_cache

# The classifier lives beside the runner, which imports every harness and the
# model SDKs behind them (``openai`` alone is ~500 modules). Imported when a
# failure is first worded, not when this contract is: the API process reaches
# it through the surfaces' reply sender, and must not pay for the runner.


def _run_failure_message(exc: BaseException) -> str:
    from app.modules.agent.services.run_finalizer import run_failure_message

    return run_failure_message(exc)


@lru_cache(maxsize=1)
def _unclassified() -> str:
    # What `run_failure_message` says about an exception it has no sentence
    # for. That line points at the model settings, which is advice for the
    # teammate's owner and not for whoever happens to be in the chat.
    return _run_failure_message(Exception())


def user_safe_failure_reason(exc: BaseException) -> str | None:
    """The failure's own user-facing sentence, or None when it has none."""
    message = _run_failure_message(exc)
    return None if message == _unclassified() else message


def user_safe_stored_reason(message: str | None) -> str | None:
    """A run's stored error sentence, or None when it is the unclassified one.

    The finished run's reply is rendered from the run row, after the
    exception is gone; this is ``user_safe_failure_reason`` for that row.
    """
    return None if not message or message == _unclassified() else message
