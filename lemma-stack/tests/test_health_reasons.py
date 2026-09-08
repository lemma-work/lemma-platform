"""What a container's exit is reported as, when its logs already explain it."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from lemma_stack.stack.health import exit_reason

# What PostgreSQL 18 actually writes when handed a pg16 data directory.
PG16_ON_PG18 = """
PostgreSQL Database directory appears to contain a database; Skipping initialization
FATAL:  database files are incompatible with server
DETAIL:  The data directory was initialized by PostgreSQL version 16, which is not compatible with this version 18.4.
"""


def _runtime(output: str):
    def run(*args, check=True, capture=True):
        assert args[0] == "logs", args
        return subprocess.CompletedProcess(list(args), 0, stdout=output, stderr="")

    return SimpleNamespace(run=run)


def test_a_postgres_major_upgrade_says_so_and_names_the_remedy() -> None:
    """The one exit here with a known fix that nobody could guess.

    `DEFAULT_INFRA_IMAGES` has moved major before and will again. Postgres
    refuses to start rather than touching files from another major — correct,
    and the reason nothing noticed: the container just exits, and the start
    sequence said only that it had. The data is intact, retrying cannot help,
    and the fix is to delete the volume on purpose.
    """
    reason = exit_reason(_runtime(PG16_ON_PG18), "lemma-local-db")

    assert "different PostgreSQL major version" in reason
    # Quoted from Postgres rather than paraphrased, so the versions are the
    # ones it actually read.
    assert "version 16" in reason and "18.4" in reason
    assert "lemma down" in reason, "a cause with no remedy is still a dead end"
    assert "erases" in reason, "deleting the volume is destructive; say so"


def test_an_ordinary_exit_is_not_dressed_up_as_a_version_problem() -> None:
    """Guessing wrong here sends someone to delete a volume they should keep."""
    reason = exit_reason(_runtime("could not bind to port 5432: address in use"), "lemma-local-db")

    assert reason == "lemma-local-db exited while waiting for it to become healthy"


def test_a_runtime_that_cannot_produce_logs_still_reports_the_exit() -> None:
    """Diagnosis is a bonus; the exit itself must always be reported."""

    def explodes(*args, check=True, capture=True):
        raise OSError("no such container")

    reason = exit_reason(SimpleNamespace(run=explodes), "lemma-local-db")
    assert reason == "lemma-local-db exited while waiting for it to become healthy"
