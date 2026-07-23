"""JSON value types shared by function domain and wire contracts."""

from pydantic import JsonValue


type JsonObject = dict[str, JsonValue]
