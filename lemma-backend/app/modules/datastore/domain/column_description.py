"""One description of a column, for every reader that has to write to it.

The context brief and the `pod_tables` tool each rendered columns their own way,
and both lost what decides whether a write lands: a `TEXT` column with a
`max_length` is `VARCHAR(n)` in the database and refuses a longer value; the
brief dropped an ENUM's allowed values; neither said a default, and neither
said that a column named `check` or `Order` cannot be written unquoted in the
SQL an agent writes by hand. One function now says all of it, so the two cannot
disagree again.

Also where a column name that SQL cannot use unquoted is recognised -- for the
describe, and for refusing such a name when a column is created.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)

# PostgreSQL's reserved key words, both the fully reserved and the ones that
# may only name a function or type (Appendix C of its manual). Either way a
# column called one of these is a syntax error in a query that does not quote it.
RESERVED_WORDS = frozenset(
    {
        "all", "analyse", "analyze", "and", "any", "array", "as", "asc",
        "asymmetric", "authorization", "binary", "both", "case", "cast", "check",
        "collate", "collation", "column", "concurrently", "constraint", "create",
        "cross", "current_catalog", "current_date", "current_role",
        "current_schema", "current_time", "current_timestamp", "current_user",
        "default", "deferrable", "desc", "distinct", "do", "else", "end",
        "except", "false", "fetch", "for", "foreign", "freeze", "from", "full",
        "grant", "group", "having", "ilike", "in", "initially", "inner",
        "intersect", "into", "is", "isnull", "join", "lateral", "leading",
        "left", "like", "limit", "localtime", "localtimestamp", "natural",
        "not", "notnull", "null", "offset", "on", "only", "or", "order",
        "outer", "overlaps", "placing", "primary", "references", "returning",
        "right", "select", "session_user", "similar", "some", "symmetric",
        "system_user", "table", "tablesample", "then", "to", "trailing", "true",
        "union", "unique", "user", "using", "variadic", "verbose", "when",
        "where", "window", "with",
    }
)  # fmt: skip

# Enough options to write a valid value from the brief alone; past it the list
# is cut, and the full set is one `pod_tables` call away.
_BRIEF_OPTIONS = 12
_BRIEF_DEFAULT_CHARS = 30


def needs_quoting(name: str) -> bool:
    """Whether hand-written SQL must put this column name in double quotes.

    Mixed case counts: the platform's own DDL quotes every name, so `Order` is
    stored as `Order`, and unquoted SQL folds it to `order` and misses.
    """
    return (
        name.lower() in RESERVED_WORDS
        or name != name.lower()
        or (bool(name) and name[0].isdigit())
    )


def column_name_problem(name: str) -> str | None:
    """Why a new column may not take this name, or None when it may.

    Only for a column being created: existing tables keep the names they have,
    and their describe marks those as needing quotes instead.
    """
    if name.lower() in RESERVED_WORDS:
        return (
            f"'{name}' is a reserved word in SQL, so every query that reads it "
            "would have to quote it. Choose another name, e.g. "
            f"'{name.lower()}_value'."
        )
    if name != name.lower():
        return (
            f"'{name}' has capital letters, and SQL folds an unquoted name to "
            f"lower case, so queries would miss it. Use '{name.lower()}'."
        )
    if name[:1].isdigit():
        return f"'{name}' starts with a digit, which SQL cannot read unquoted."
    return None


@dataclass(frozen=True, slots=True)
class ColumnDescription:
    name: str
    type: str
    #: The type as the database holds it, where that differs: `VARCHAR(500)`.
    sql_type: str | None
    required: bool
    unique: bool
    #: Filled in by the platform; a write must not supply it.
    auto: bool
    computed: bool
    default: bool | int | float | str | None
    max_length: int | None
    options: tuple[str, ...]
    references: str | None
    quote_required: bool
    description: str | None

    def as_json(self) -> dict[str, object]:
        """The full description, leaving out what does not apply."""
        described: dict[str, object] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
        }
        optional: dict[str, object | None] = {
            "sql_type": self.sql_type,
            "max_length": self.max_length,
            "options": list(self.options) or None,
            "default": self.default,
            "references": self.references,
            "description": self.description,
        }
        described.update({k: v for k, v in optional.items() if v is not None})
        flags = {
            "unique": self.unique,
            "auto": self.auto,
            "computed": self.computed,
            "quote_required": self.quote_required,
        }
        described.update({k: True for k, v in flags.items() if v})
        return described

    def brief(self) -> str:
        """One compact `name:TYPE(marks)` entry for the context brief.

        Write constraints only; descriptions stay on inspection.
        """
        name = f'"{self.name}"' if self.quote_required else self.name
        type_name = self.sql_type or self.type
        if self.options:
            shown = list(self.options[:_BRIEF_OPTIONS])
            if len(self.options) > _BRIEF_OPTIONS:
                shown.append(f"…+{len(self.options) - _BRIEF_OPTIONS}")
            type_name += "[" + "|".join(shown) + "]"
        marks: list[str] = []
        if self.auto:
            marks.append("auto")
        elif self.computed:
            marks.append("computed")
        elif self.required:
            marks.append("required")
        if self.unique:
            marks.append("unique")
        if self.references:
            marks.append(f"→{self.references}")
        if self.default is not None and not self.auto:
            marks.append(f"default={str(self.default)[:_BRIEF_DEFAULT_CHARS]}")
        spec = f"{name}:{type_name}"
        return spec + ("(" + " ".join(marks) + ")" if marks else "")


def describe_column(column: ColumnSchema) -> ColumnDescription:
    type_name = column.type.value
    sized = column.max_length and column.type in {
        DatastoreDataType.TEXT,
        DatastoreDataType.FILE_PATH,
    }
    return ColumnDescription(
        name=column.name,
        type=type_name,
        sql_type=f"VARCHAR({column.max_length})" if sized else None,
        required=column.required,
        unique=column.unique,
        auto=column.system or column.auto,
        computed=column.computed,
        default=column.default,
        max_length=column.max_length,
        options=tuple(column.options or ()),
        references=column.foreign_key.references if column.foreign_key else None,
        quote_required=needs_quoting(column.name),
        description=column.description,
    )
