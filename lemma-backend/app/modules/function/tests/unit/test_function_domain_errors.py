"""Reachability-first coverage for ``domain/errors.py``.

Before adding tests here, every ``Function*Error`` class was grepped across the
whole repo (production code, not just this module) for a ``raise`` site. A
whole "resource access" family -- ``FunctionAccessDeniedError`` and nine
others describing per-function datastore/file/connector access control --
had zero raise sites anywhere: leftovers from before that enforcement moved
into the sandbox-side SDK. They've since been deleted rather than tested,
since testing an ``__init__`` nothing ever constructs isn't coverage.

``FunctionConflictError`` is the one class in that neighborhood that IS
reachable (``FunctionService._create_function_checked`` raises it on a
duplicate name), and is already exercised end-to-end via
``test_function_e2e.py::test_create_function_rejects_duplicate_name_in_same_pod``
(which asserts the HTTP-mapped ``409`` + ``FUNCTION_CONFLICT`` code). This adds
a direct, fast check of the error's own shape so that mapping does not have to
be re-derived from an HTTP response every time.
"""

from __future__ import annotations

from app.modules.function.domain.errors import FunctionConflictError

import pytest

pytestmark = pytest.mark.unit


def test_function_conflict_error_carries_the_409_conflict_shape() -> None:
    error = FunctionConflictError("Function with name 'reporter' already exists")

    assert error.status_code == 409
    assert error.code == "FUNCTION_CONFLICT"
    assert "reporter" in str(error)
