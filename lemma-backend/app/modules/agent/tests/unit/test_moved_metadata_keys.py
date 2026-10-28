"""No code reads or writes a metadata key that has a typed column now.

A conversation's, run's or message's behaviour-driving metadata keys moved to
columns (migration 0066). The JSON still holds them for one release, so a
reader that went back to the key would keep working -- on rows written before
the move -- and quietly read nothing on every row written after it. This holds
every ``conversation_metadata``, ``run_metadata`` and ``message_metadata``
access in ``app/`` against the moved-key lists in ``domain/conversations``.

What counts as an access: a subscript, ``.get``/``.has_key``/``.pop``/
``.setdefault``, ``.contains({...})``, ``key in ...``, ``jsonb_set(column,
array([key]), ...)``, and a ``column={...}`` keyword -- on an expression that
names one of the three columns, or on a local name assigned from one. Keys are
string literals or module-level string constants, anywhere in ``app/``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.modules.agent.domain.conversations import (
    MOVED_CONVERSATION_KEYS,
    MOVED_MESSAGE_KEYS,
    MOVED_RUN_KEYS,
)

pytestmark = pytest.mark.unit

_APP = Path(__file__).resolve().parents[4]

MOVED_KEYS: dict[str, frozenset[str]] = {
    "conversation_metadata": MOVED_CONVERSATION_KEYS,
    "run_metadata": MOVED_RUN_KEYS,
    "message_metadata": MOVED_MESSAGE_KEYS,
}

_KEY_METHODS = frozenset({"get", "has_key", "pop", "setdefault"})


def _sources() -> Iterator[Path]:
    for path in sorted(_APP.rglob("*.py")):
        if "tests" in path.relative_to(_APP).parts:
            continue
        yield path


def _string_constants(trees: dict[Path, ast.Module]) -> dict[str, set[str]]:
    """Every module-level ``NAME = "literal"`` in ``app/``, by name."""
    constants: dict[str, set[str]] = {}
    for tree in trees.values():
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants.setdefault(target.id, set()).add(node.value.value)
    return constants


class _Checker:
    def __init__(self, constants: dict[str, set[str]]) -> None:
        self.constants = constants

    def keys(self, node: ast.AST | None) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Name):
            return set(self.constants.get(node.id, ()))
        if isinstance(node, ast.Tuple | ast.List):
            return set().union(*(self.keys(element) for element in node.elts))
        if isinstance(node, ast.Call) and node.args:
            # `array([KEY])`
            return self.keys(node.args[0])
        return set()

    @staticmethod
    def columns(node: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
        found: set[str] = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and inner.attr in MOVED_KEYS:
                found.add(inner.attr)
            elif isinstance(inner, ast.Name) and inner.id in aliases:
                found |= aliases[inner.id]
        return found

    def accesses(
        self, node: ast.AST, aliases: dict[str, set[str]]
    ) -> Iterator[tuple[set[str], set[str]]]:
        """``(columns, keys)`` for each access ``node`` makes."""
        if isinstance(node, ast.Subscript):
            yield self.columns(node.value, aliases), self.keys(node.slice)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = node.func.value
            if node.func.attr in _KEY_METHODS and node.args:
                yield self.columns(base, aliases), self.keys(node.args[0])
            elif node.func.attr == "contains" and node.args:
                argument = node.args[0]
                if isinstance(argument, ast.Dict):
                    keys = set().union(*(self.keys(key) for key in argument.keys))
                    yield self.columns(base, aliases), keys
            elif node.func.attr == "jsonb_set" and len(node.args) >= 2:
                yield self.columns(node.args[0], aliases), self.keys(node.args[1])
        elif isinstance(node, ast.Compare) and any(
            isinstance(op, ast.In | ast.NotIn) for op in node.ops
        ):
            for comparator in node.comparators:
                yield self.columns(comparator, aliases), self.keys(node.left)
        elif isinstance(node, ast.keyword) and node.arg in MOVED_KEYS:
            if isinstance(node.value, ast.Dict):
                keys = set().union(*(self.keys(key) for key in node.value.keys))
                yield {node.arg}, keys

    def violations(self, tree: ast.Module) -> Iterator[tuple[int, str, str]]:
        scopes: list[ast.AST] = [tree] + [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        seen: set[tuple[int, str, str]] = set()
        for scope in scopes:
            aliases: dict[str, set[str]] = {}
            body = _own_nodes(scope)
            for node in body:
                if isinstance(node, ast.Assign):
                    touched = self.columns(node.value, {})
                    for target in node.targets:
                        if touched and isinstance(target, ast.Name):
                            aliases.setdefault(target.id, set()).update(touched)
            for node in body:
                for columns, keys in self.accesses(node, aliases):
                    for column in columns:
                        for key in keys & MOVED_KEYS[column]:
                            line = getattr(node, "lineno", 0)
                            if (line, column, key) not in seen:
                                seen.add((line, column, key))
                                yield line, column, key


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """``scope`` and what is under it, short of the functions defined inside:
    a name one function assigns is not the same name in another."""
    found: list[ast.AST] = []
    pending: list[ast.AST] = [scope]
    while pending:
        node = pending.pop()
        found.append(node)
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                pending.append(child)
    return found


def _scan(sources: dict[Path, str]) -> list[str]:
    trees = {path: ast.parse(text) for path, text in sources.items()}
    checker = _Checker(_string_constants(trees))
    return [
        f"{path}:{line}: {column}[{key!r}]"
        for path, tree in trees.items()
        for line, column, key in checker.violations(tree)
    ]


def test_no_code_touches_a_metadata_key_that_has_a_column() -> None:
    texts = {path: path.read_text(encoding="utf-8") for path in _sources()}
    # Accesses only where a column is named; key constants from there and from
    # the agent module, which names the keys.
    touching = {
        path: ast.parse(text)
        for path, text in texts.items()
        if any(column in text for column in MOVED_KEYS)
    }
    naming = {
        path: touching.get(path) or ast.parse(texts[path])
        for path in texts
        if path.is_relative_to(_APP / "modules" / "agent")
    }
    checker = _Checker(_string_constants({**naming, **touching}))
    found = [
        f"{path.relative_to(_APP.parent)}:{line}: {column}[{key!r}]"
        for path, tree in touching.items()
        for line, column, key in checker.violations(tree)
    ]
    assert found == [], (
        "These read or write a metadata key that is a typed column now; use the "
        "column (`domain/conversations` lists the moved keys):\n" + "\n".join(found)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'MessageModel.message_metadata["steered_into_run"].astext',
        '(row.run_metadata or {}).get("source")',
        'ConversationModel.conversation_metadata.has_key("ask")',
        'ConversationModel.conversation_metadata.contains({"ask": {}})',
        '"cwd" in conversation.conversation_metadata',
        'func.jsonb_set(Model.run_metadata, array(["failure"]), v, True)',
        'update(MessageModel).values(message_metadata={"private_note": True})',
        'KEY = "todos"\nModel.conversation_metadata[KEY]',
        (
            "def f():\n    metadata = MessageModel.message_metadata\n"
            '    return metadata["client_message_id"]'
        ),
    ],
)
def test_the_check_sees_each_kind_of_access(snippet: str) -> None:
    assert _scan({Path("snippet.py"): snippet})


@pytest.mark.parametrize(
    "snippet",
    [
        # A key that stays in metadata.
        'MessageModel.message_metadata["voice_transcripts"]',
        # `source` moved for conversations and runs, not for messages.
        'MessageModel.message_metadata["source"].astext',
        # A request's metadata is not a stored column.
        '(message_metadata or {}).get("client_message_id")',
    ],
)
def test_the_check_leaves_what_did_not_move(snippet: str) -> None:
    assert _scan({Path("snippet.py"): snippet}) == []
