from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import typer

from .state import err_console as console

# `--data -` (and `--credentials-json -`, …) reads the payload from stdin, so an
# agent can pipe one command's `--json` output straight into the next without a
# temp file. `--file` keeps `exists=True` typer validation and takes real paths.
STDIN_TOKEN = "-"


def read_json(
    value: str | None, file: Path | None, *, required: bool = False
) -> dict[str, Any]:
    if value and file:
        raise typer.BadParameter("Use only one of --data or --file.")
    if file is not None:
        raw = file.read_text(encoding="utf-8")
    elif value == STDIN_TOKEN:
        raw = sys.stdin.read()
        if not raw.strip():
            raise typer.BadParameter("Reading --data from stdin, but stdin was empty.")
    elif value is not None:
        raw = value
    elif required:
        raise typer.BadParameter("Provide --data or --file.")
    else:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("JSON payload must be an object.")
    return parsed


# Resource types that expose a `lemma <type> schema` command (see _authoring);
# a validation error for one points the user at it for the full shape + enums.
_SCHEMA_RESOURCES = frozenset(
    {"agent", "function", "table", "workflow", "schedule", "surface"}
)


@lru_cache(maxsize=None)
def accepted_field_names(model_cls: Any) -> frozenset[str] | None:
    """The wire-level field names a generated SDK request model declares, or
    None when ``model_cls`` isn't one of them (nothing to check).

    The generated models are attrs classes whose Python attribute names match
    the JSON keys except for a single trailing underscore on reserved words
    (``type_`` <- ``"type"``). That mapping is exact across every model in the
    SDK — `tests/test_payload.py` re-derives it from each `from_dict` body and
    asserts the two agree, so this stays honest if the generator changes.

    ``additional_properties`` is excluded deliberately: models generated for an
    endpoint that tolerates extra keys stash unknown input there and forward it,
    but the server ignores what its schema doesn't declare. Forwarded-and-ignored
    is still silently dropped from the caller's point of view, so it gets the
    same warning as a key the model drops outright.

    Read through ``__attrs_attrs__`` rather than ``attrs.fields`` so the CLI
    keeps no direct attrs dependency of its own (it arrives with lemma-sdk), and
    so a non-attrs request class degrades to "no check" instead of raising.
    """
    fields = getattr(model_cls, "__attrs_attrs__", None)
    if fields is None:
        return None
    return frozenset(
        name[:-1] if name.endswith("_") else name
        for name in (field.name for field in fields)
        if name != "additional_properties"
    )


def ignored_fields(model_cls: Any, data: dict[str, Any]) -> list[str]:
    """Keys in ``data`` the API request has no field for — i.e. everything that
    would vanish between here and the server."""
    accepted = accepted_field_names(model_cls)
    if accepted is None:
        return []
    return sorted(key for key in data if key not in accepted)


def _schema_hint(context: str | None) -> str:
    if not context:
        return ""
    resource = context.split()[0]
    if resource not in _SCHEMA_RESOURCES:
        return ""
    return f" Run `lemma {resource} schema` for the required fields and valid enums."


def build_request(
    model_cls: Any,
    data: dict[str, Any],
    *,
    context: str | None = None,
    strict: bool = False,
) -> Any:
    """Construct an SDK request model from a dict, turning a missing/mistyped
    field into an actionable `ValueError` instead of a raw `KeyError`/`TypeError`,
    and never letting an unrecognized field disappear in silence.

    Keeping this at the construction site lets `run_with_client` stay narrow:
    genuine bugs elsewhere still surface as tracebacks, while a hand-written
    payload missing a required field reports exactly which one. `context` (e.g.
    "agent triage") is appended so bundle imports name the offending resource,
    and — when it names a resource with a schema command — points at it.

    The generated models silently drop every key they don't declare, so a typo
    (or a field the endpoint simply doesn't accept, like `permissions` on
    function create) used to produce a successful call that quietly did less
    than asked. Unknown keys now warn; with ``strict`` they raise, because in a
    bundle import an unrecognized key is always an authoring mistake and there
    is no human watching the output.
    """
    where = f" ({context})" if context else ""
    hint = _schema_hint(context)
    ignored = ignored_fields(model_cls, data)
    if ignored:
        listed = ", ".join(ignored)
        if strict:
            raise ValueError(
                f"Unrecognized field(s){where}: {listed}. The API request has no "
                f"such field, so they would be dropped silently.{hint}"
            )
        console.print(
            f"[yellow]warning[/yellow]{where or ' request'}: ignored unrecognized "
            f"field(s) {listed} — the API has no such field, so they were NOT "
            f"sent.{hint}"
        )
    try:
        return model_cls.from_dict(data)
    except KeyError as exc:
        key = exc.args[0] if exc.args else ""
        field = f": {key}" if key else ""
        raise ValueError(f"Missing required field{field}.{where}{hint}") from exc
    except TypeError as exc:
        raise ValueError(f"Invalid field value{where}: {exc}{hint}") from exc
