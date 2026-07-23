"""Strict JSON value types for the function runtime protocol."""

from pydantic import JsonValue


type JsonObject = dict[str, JsonValue]
