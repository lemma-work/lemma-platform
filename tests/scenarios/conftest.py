"""Fixtures for the product scenario suite.

All of them live in `harness/plugin.py`, so a suite built on this one gets the
same fixtures by naming the same plugin. See its docstring, and
`harness/hookspecs.py` for what such a suite may change.
"""

pytest_plugins = ["harness.reporting", "harness.plugin"]
