"""Payload construction: never lose a field in silence.

The generated SDK request models pop the keys they declare and drop the rest, so
a payload key the endpoint has no field for used to vanish between the CLI and
the server with a clean exit code. That is how `lemma functions create` could
accept `permissions.grants` (its own help advertised it), report success, and
create a function with zero access.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re

import pytest
import typer

import lemma_sdk.openapi_client.models as sdk_models
from lemma_sdk.openapi_client.models.create_agent_request import CreateAgentRequest
from lemma_sdk.openapi_client.models.create_function_request import (
    CreateFunctionRequest,
)
from lemma_sdk.openapi_client.models.create_table_request import CreateTableRequest

from lemma_cli.cli_core.payload import (
    accepted_field_names,
    build_request,
    ignored_fields,
    read_json,
)


_POP_RE = re.compile(r'd\.pop\(\s*"([^"]+)"')


def _sdk_model_classes():
    for module_info in pkgutil.iter_modules(sdk_models.__path__):
        module = importlib.import_module(f"{sdk_models.__name__}.{module_info.name}")
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and hasattr(cls, "__attrs_attrs__"):
                yield cls


def test_accepted_field_names_matches_every_sdk_model_from_dict():
    """The attrs-name -> wire-name mapping `accepted_field_names` relies on (strip
    one trailing underscore) must hold for EVERY generated model, or the
    unknown-field check would flag a legitimate key.

    Re-derives the truth from each `from_dict` body, so a change in the code
    generator fails here instead of producing bogus warnings in the field.
    """
    checked = 0
    mismatches = []
    for cls in _sdk_model_classes():
        try:
            source = inspect.getsource(cls.from_dict)
        except (OSError, TypeError):  # pragma: no cover - source always present
            continue
        wire = set(_POP_RE.findall(source))
        if not wire:
            continue
        checked += 1
        declared = accepted_field_names(cls)
        if wire != declared:
            mismatches.append((cls.__name__, sorted(wire ^ (declared or set()))))
    assert checked > 100, f"expected to check the model tree, only saw {checked}"
    assert mismatches == []


def test_ignored_fields_flags_keys_the_request_has_no_slot_for():
    # The exact payload that used to create a grant-less function.
    data = {
        "name": "maybe_rewrite_lesson",
        "code": "#function_name: maybe_rewrite_lesson\n",
        "permissions": {"grants": [{"resource_type": "datastore_table"}]},
    }
    assert ignored_fields(CreateFunctionRequest, data) == ["permissions"]
    # Agents declare `permissions`, so it is not ignored there.
    assert ignored_fields(
        CreateAgentRequest, {"name": "a", "instruction": "i", "permissions": {}}
    ) == []


def test_ignored_fields_ignores_additional_properties_slot():
    """A model with an `additional_properties` bag forwards unknown keys, but the
    server drops what its schema doesn't declare — still a silent loss, so it is
    still reported."""
    assert "nope" in ignored_fields(CreateTableRequest, {"name": "t", "nope": 1})


def test_build_request_warns_but_proceeds_for_ad_hoc_commands(capsys):
    request = build_request(
        CreateFunctionRequest,
        {"name": "f", "code": "x", "permissions": {"grants": []}},
        context="function f",
    )
    out = capsys.readouterr().out
    assert "permissions" in out
    assert "NOT" in out  # "were NOT sent"
    # The rest of the payload still goes through.
    assert request.to_dict() == {"name": "f", "code": "x"}


def test_build_request_raises_in_strict_mode():
    """A bundle import runs unattended, and an unrecognized key there is always an
    authoring mistake — so it fails the import instead of printing into the void."""
    with pytest.raises(ValueError, match="Unrecognized field"):
        build_request(
            CreateFunctionRequest,
            {"name": "f", "code": "x", "revision_hash": "abc"},
            context="function f",
            strict=True,
        )


def test_build_request_still_reports_missing_required_fields():
    with pytest.raises(ValueError, match="Missing required field"):
        build_request(CreateFunctionRequest, {"code": "x"}, context="function f")


def test_build_request_is_quiet_for_a_clean_payload(capsys):
    build_request(CreateFunctionRequest, {"name": "f", "code": "x"}, context="function f")
    assert capsys.readouterr().out == ""


def test_read_json_accepts_stdin(monkeypatch):
    """`-` lets one command's --json output pipe into the next without a temp file."""
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO('{"grants": []}'))
    assert read_json("-", None) == {"grants": []}


def test_read_json_rejects_empty_stdin(monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("   "))
    with pytest.raises(typer.BadParameter, match="stdin was empty"):
        read_json("-", None)
