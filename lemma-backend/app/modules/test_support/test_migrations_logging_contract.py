"""Contract: the migrate container must not report ordinary progress as errors.

The log collector classifies whatever a container writes to stderr as ERROR.
``alembic.ini`` sets ``[logger_alembic] level = INFO``, so pointing the console
handler at stderr turned every "Running upgrade abc -> def" into an ERROR record
in both environments — enough to move the error rate on its own, and the same
class of noise as a deprecation warning arriving at ERROR severity.

Nothing else covers ``alembic.ini``: it is read by Alembic's own logging setup,
not by the application's, so no import ever exercises it.
"""

from __future__ import annotations

import configparser
from pathlib import Path

# app/modules/test_support/this_file.py -> lemma-backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"


def _alembic_config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    # Alembic's own interpolation syntax (%(here)s) is not our concern here.
    parser.read_string(_ALEMBIC_INI.read_text())
    return parser


def test_alembic_console_handler_writes_to_stdout():
    config = _alembic_config()

    assert config.has_section("handler_console"), (
        "alembic.ini lost its console handler; the migrate container would log nowhere"
    )
    args = config.get("handler_console", "args")

    assert "sys.stdout" in args, (
        f"alembic.ini [handler_console] args = {args}. Migration progress logs at "
        "INFO, and anything on stderr is collected as ERROR — so stderr here "
        "reports every successful migration as a container error."
    )
    assert "sys.stderr" not in args


def test_alembic_still_logs_migration_progress():
    """Guard the other direction: silencing it is not the fix for the noise."""
    config = _alembic_config()

    assert config.get("logger_alembic", "level") == "INFO", (
        "Migration progress is worth logging — the bug was the stream it went "
        "to, not that it was emitted."
    )
