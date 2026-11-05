"""The agent module's use cases: one verb per file, each owning its transaction.

A use case is constructed with exactly what it uses -- repositories, helpers
from ``services/``, and the other use cases it calls -- and built from a unit of
work by its own ``build(uow)``. Controllers (through ``api/dependencies``), work
handlers, contracts and other use cases call one directly; none is reached
through another object's attribute.
"""
