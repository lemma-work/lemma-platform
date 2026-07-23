"""Deterministic identities derived from the public function-run identity."""

from __future__ import annotations

from uuid import UUID


def function_run_job_id(run_id: UUID) -> str:
    """Return the one queue identity for an asynchronous function run."""

    return f"function:{run_id}"
