"""No file in ``agent/contracts/`` runs SQL.

A contract is what another module may ask of this one, and it answers through
a use case or a repository. When contracts ran their own statements, four of
them wrote ``agent_conversations`` from outside every repository, so no
conversation invariant could be enforced in one place. This holds every
contract to that: no SQLAlchemy statement built (``select``, ``insert``,
``update``, ``delete``, ``text``), and nothing run on a session.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
_STATEMENTS = frozenset({"select", "insert", "update", "delete", "text"})
_SESSION_CALLS = frozenset(
    {"execute", "scalar", "scalars", "get", "add", "delete", "flush", "merge"}
)


def _sql_in(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "sqlalchemy"
        ):
            found.append((node.lineno, f"imports {node.module}"))
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _STATEMENTS:
            found.append((node.lineno, f"{func.id}(...)"))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in _SESSION_CALLS
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "session"
        ):
            found.append((node.lineno, f"session.{func.attr}(...)"))
    return found


def test_no_contract_runs_sql() -> None:
    found = [
        f"{path.name}:{line}: {what}"
        for path in sorted(_CONTRACTS.glob("*.py"))
        for line, what in _sql_in(path.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "A contract answers through a use case or a repository; move this "
        "statement into one:\n" + "\n".join(found)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "from sqlalchemy import select\n",
        "rows = select(Model.id)\n",
        "await uow.session.execute(stmt)\n",
        "await self.uow.session.scalar(stmt)\n",
        "await uow.session.get(Model, 1)\n",
        "update(Model).values(x=1)\n",
    ],
)
def test_the_check_sees_each_kind_of_sql(snippet: str) -> None:
    assert _sql_in(snippet)


def test_a_call_into_a_repository_is_not_sql() -> None:
    assert not _sql_in("await Conversations(uow).get_conversation(cid)\n")
