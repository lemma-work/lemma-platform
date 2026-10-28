"""A PATCH body that refuses what it cannot change, by name.

Pydantic drops unknown keys by default, so a PATCH naming a field it cannot
change answered 200 with nothing changed -- which reads to the caller as a
change that was made. These bodies answer 422 naming each such field, and say
which ones are fixed for life rather than merely unknown.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, model_validator


class StrictPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Fields the resource has and keeps for life.
    fixed_fields: ClassVar[frozenset[str]] = frozenset()
    #: What to do instead of changing one of them.
    fixed_remedy: ClassVar[str] = "create a new one instead"

    @model_validator(mode="before")
    @classmethod
    def _only_changeable(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        keys = {str(key) for key in data}
        fixed = sorted(cls.fixed_fields & keys)
        unknown = sorted(keys - set(cls.model_fields) - cls.fixed_fields)
        problems: list[str] = []
        if fixed:
            problems.append(
                f"{', '.join(fixed)} cannot be changed ({cls.fixed_remedy})"
            )
        if unknown:
            problems.append(f"not fields of this resource: {', '.join(unknown)}")
        if problems:
            raise ValueError("; ".join(problems))
        return data


__all__ = ["StrictPatch"]
