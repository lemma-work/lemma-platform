def test_deep_resolve_refs_leaves_the_caller_spec_untouched():
    """The copy that made this quadratic was also unnecessary.

    Every dict and list in the result is built fresh by the comprehensions, and
    only immutable scalars are shared, so resolving cannot reach back into the
    caller's document. Asserted directly rather than trusted, because the whole
    argument for deleting the deepcopy rests on it.
    """
    import copy as copy_module

    from app.modules.connectors.infrastructure.openapi.spec_helpers import (
        deep_resolve_refs,
    )

    spec = {
        "components": {
            "schemas": {
                "Address": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
                "User": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "home": {"$ref": "#/components/schemas/Address"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        }
    }
    before = copy_module.deepcopy(spec)

    resolved = deep_resolve_refs(spec, {"$ref": "#/components/schemas/User"})

    assert resolved["properties"]["home"]["properties"]["city"] == {"type": "string"}
    assert spec == before, "resolving mutated the caller's spec"

    # And mutating the result must not reach the original either. Asserted on
    # the two nodes the mutations above target rather than by re-comparing the
    # whole document: naming them says which sharing would have been missed, and
    # a repeat of the previous comparison reads as redundant because, to anything
    # that cannot see the aliasing being tested for, it is.
    resolved["properties"]["home"]["properties"]["city"]["type"] = "number"
    resolved["properties"]["tags"]["items"]["type"] = "number"
    schemas = spec["components"]["schemas"]
    assert schemas["Address"]["properties"]["city"]["type"] == "string", (
        "mutating the resolved $ref reached back into the referenced schema"
    )
    assert schemas["User"]["properties"]["tags"]["items"]["type"] == "string", (
        "mutating the resolved result reached back into the caller's spec"
    )
