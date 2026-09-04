"""A `default` in a trigger's `config_schema` has to be readable server-side.

Left unread it is decoration: the connect form prefills it, and a resource
created through the API or the CLI with an empty config gets nothing. GitHub's
`workflow_run` is why that matters -- a busy repository emits one delivery per
run per state change, so the API path would wake an agent three times for one
CI run while the UI path woke it once.

Which of these defaults survive is the caller's decision, and is tested where
that decision is made.
"""

from __future__ import annotations

from app.modules.connectors.contracts.triggers import _declared_defaults


def test_a_declared_default_is_read_out_of_the_schema():
    schema = {"type": "object", "properties": {"actions": {"default": ["completed"]}}}

    assert _declared_defaults(schema) == {"actions": ["completed"]}


def test_a_property_with_no_default_stays_absent():
    schema = {"type": "object", "properties": {"repository_id": {"type": "integer"}}}

    assert _declared_defaults(schema) == {}


def test_a_trigger_with_no_schema_declares_nothing():
    assert _declared_defaults(None) == {}
    assert _declared_defaults({}) == {}


def test_a_schema_whose_properties_are_not_an_object_declares_nothing():
    """Trigger catalogs are provider-supplied, so the shape is not guaranteed."""
    assert _declared_defaults({"properties": ["actions"]}) == {}
