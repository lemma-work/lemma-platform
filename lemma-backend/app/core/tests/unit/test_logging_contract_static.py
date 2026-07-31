"""Static enforcement for every server-process application log call."""

from __future__ import annotations

import ast
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re
import sys

from app.core.log.event_catalog import EVENT_CATALOG as BACKEND_CATALOG


REPO_ROOT = Path(__file__).resolve().parents[5]
EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
PROHIBITED_FIELD_PARTS = {
    "authorization",
    "body",
    "content",
    "cookie",
    "email",
    "headers",
    "message",
    "password",
    "payload",
    "prompt",
    "query",
    "request",
    "response",
    "secret",
    "source_text",
    "sql",
    "stack",
    "text",
    "token",
    "traceback",
    "uri",
    "url",
}


def _load_agentbox_catalog():
    path = REPO_ROOT / "agentbox" / "agentbox" / "event_catalog.py"
    spec = spec_from_file_location("agentbox_event_catalog_for_test", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.EVENT_CATALOG


def _logger_method(node: ast.Call) -> str | None:
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in METHODS:
        return None
    receiver = node.func.value
    if isinstance(receiver, ast.Name) and receiver.id in {"logger", "fs_logger"}:
        return node.func.attr
    if isinstance(receiver, ast.Attribute) and receiver.attr in {"logger", "_logger"}:
        return node.func.attr
    if (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == "get_logger"
    ):
        return node.func.attr
    return None


def _runtime_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts or "test_support" in path.parts:
            continue
        yield path


def test_every_runtime_log_call_matches_its_exact_catalog_contract() -> None:
    roots = {
        REPO_ROOT / "lemma-backend" / "app": BACKEND_CATALOG,
        REPO_ROOT / "lemma-backend" / "scripts": BACKEND_CATALOG,
        REPO_ROOT / "agentbox" / "agentbox": _load_agentbox_catalog(),
    }
    failures: list[str] = []
    seen_by_catalog: dict[int, set[str]] = {id(catalog): set() for catalog in roots.values()}

    for root, catalog in roots.items():
        for path in _runtime_files(root):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                method = _logger_method(node)
                if method is None:
                    continue
                location = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                if method in {"exception", "critical"}:
                    failures.append(f"{location}: use error with bounded exc_info")
                    continue
                if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                    failures.append(f"{location}: event must be a literal string")
                    continue
                event = node.args[0].value
                seen_by_catalog[id(catalog)].add(event)
                if not EVENT_RE.fullmatch(event):
                    failures.append(f"{location}: unstable event {event!r}")
                if len(node.args) != 1:
                    failures.append(f"{location}: positional interpolation is forbidden")
                if any(keyword.arg is None for keyword in node.keywords):
                    failures.append(f"{location}: unrestricted **kwargs are forbidden")
                fields = {
                    keyword.arg
                    for keyword in node.keywords
                    if keyword.arg not in {None, "exc_info", "stack_info"}
                }
                unsafe = {
                    field
                    for field in fields
                    if any(part in field.lower() for part in PROHIBITED_FIELD_PARTS)
                    and field not in {"error_type", "error_code", "error_stack_hash"}
                }
                if unsafe:
                    failures.append(f"{location}: prohibited fields {sorted(unsafe)}")
                if sum(field.endswith("_id") for field in fields) > 3:
                    failures.append(f"{location}: more than three domain identifiers")
                specification = catalog.get(event)
                if specification is None:
                    failures.append(f"{location}: unregistered event {event!r}")
                    continue
                if specification.level != method:
                    failures.append(
                        f"{location}: {event!r} must use {specification.level}, not {method}"
                    )
                if not fields <= set(specification.fields):
                    failures.append(
                        f"{location}: fields outside allowlist {sorted(fields - set(specification.fields))}"
                    )

    for catalog in roots.values():
        stale = set(catalog) - seen_by_catalog[id(catalog)] - {"logging.contract.violation"}
        if stale:
            failures.append(f"catalog contains stale events: {sorted(stale)}")
    assert not failures, "\n" + "\n".join(failures)


def test_runtime_does_not_create_framework_owned_application_loggers() -> None:
    failures: list[str] = []
    for root in (
        REPO_ROOT / "lemma-backend" / "app",
        REPO_ROOT / "lemma-backend" / "scripts",
        REPO_ROOT / "agentbox" / "agentbox",
    ):
        for path in _runtime_files(root):
            if path == REPO_ROOT / "lemma-backend" / "app" / "core" / "log" / "log.py":
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not any(isinstance(target, ast.Name) and target.id == "logger" for target in node.targets):
                    continue
                value = node.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "logging"
                    and value.func.attr == "getLogger"
                ):
                    failures.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not failures, "application logger bypasses owned contract:\n" + "\n".join(failures)


def test_runtime_async_tasks_choose_inherited_or_clean_context_explicitly() -> None:
    allowed_helpers = {
        REPO_ROOT / "lemma-backend" / "app" / "core" / "request_context.py",
        REPO_ROOT / "agentbox" / "agentbox" / "observability.py",
    }
    failures: list[str] = []
    for root in (
        REPO_ROOT / "lemma-backend" / "app",
        REPO_ROOT / "lemma-backend" / "scripts",
        REPO_ROOT / "agentbox" / "agentbox",
    ):
        for path in _runtime_files(root):
            if path in allowed_helpers:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(
                    node.func, ast.Attribute
                ):
                    continue
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"asyncio", "_asyncio"}
                    and node.func.attr == "create_task"
                ):
                    failures.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not failures, "raw create_task bypasses context policy:\n" + "\n".join(
        failures
    )
