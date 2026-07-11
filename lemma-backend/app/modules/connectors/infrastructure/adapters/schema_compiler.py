"""Restricted Pydantic-snippet to JSON-Schema translator.

Catalog snippets are parsed, never imported or executed. Only declarative
``BaseModel`` classes and an allowlisted annotation/``Field`` subset are
accepted.
"""

from __future__ import annotations

import ast
from typing import Any

import structlog

from app.modules.connectors.domain.ports import SchemaCompilerPort


_ALLOWED_IMPORTS = frozenset(
    {"__future__", "pydantic", "typing", "typing_extensions", "datetime", "uuid"}
)
_PRIMITIVES = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "Any": {},
    "UUID": {"type": "string", "format": "uuid"},
    "datetime": {"type": "string", "format": "date-time"},
    "date": {"type": "string", "format": "date"},
}
_CONTAINER_NAMES = frozenset(
    {"list", "List", "set", "Set", "tuple", "Tuple", "dict", "Dict"}
)
_FIELD_CONSTRAINTS = {
    "description": "description",
    "title": "title",
    "examples": "examples",
    "min_length": "minLength",
    "max_length": "maxLength",
    "ge": "minimum",
    "gt": "exclusiveMinimum",
    "le": "maximum",
    "lt": "exclusiveMaximum",
    "pattern": "pattern",
}


class UnsafeSchemaSnippet(ValueError):
    pass


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _literal(node: ast.expr) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError) as exc:
        raise UnsafeSchemaSnippet("Field metadata must be a literal") from exc


def _annotation_name(node: ast.expr) -> str | None:
    """Normalize the AST's constant-form ``None`` into an annotation name."""
    if isinstance(node, ast.Constant) and node.value is None:
        return "None"
    return _name(node)


class _RestrictedSchemaParser:
    def __init__(self, code: str) -> None:
        self.code = code
        self.tree = ast.parse(code)
        self.models: dict[str, ast.ClassDef] = {}
        self.target = self._target_variable()

    def _target_variable(self) -> str | None:
        for line in self.code.splitlines():
            if line.strip().startswith("#target_variable:"):
                return line.split(":", 1)[1].strip() or None
        return None

    def parse(self) -> dict[str, Any]:
        self._validate_top_level()
        selected = self._select_model()
        if selected is None:
            return {}

        schemas = {name: self._model_schema(model) for name, model in self.models.items()}
        selected_schema = schemas[selected]
        referenced = self._referenced_model_names(self.models[selected])
        recursive = selected in referenced
        if recursive:
            return {"$defs": schemas, "$ref": f"#/$defs/{selected}"}
        definitions = {
            name: schema for name, schema in schemas.items() if name in referenced
        }
        if definitions:
            selected_schema = {**selected_schema, "$defs": definitions}
        return selected_schema

    def _validate_top_level(self) -> None:
        for node in self.tree.body:
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".", 1)[0] not in _ALLOWED_IMPORTS:
                    raise UnsafeSchemaSnippet(f"Import is not allowed: {node.module}")
                continue
            if isinstance(node, ast.Import):
                if any(alias.name.split(".", 1)[0] not in _ALLOWED_IMPORTS for alias in node.names):
                    raise UnsafeSchemaSnippet("Import is not allowed")
                continue
            if isinstance(node, ast.ClassDef):
                if node.decorator_list:
                    raise UnsafeSchemaSnippet("Class decorators are not allowed")
                bases = {_name(base) for base in node.bases}
                if "BaseModel" in bases or "pydantic.BaseModel" in bases:
                    self._validate_model(node)
                    self.models[node.name] = node
                    continue
                raise UnsafeSchemaSnippet("Only BaseModel classes are allowed")
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            raise UnsafeSchemaSnippet(
                f"Executable statement is not allowed: {type(node).__name__}"
            )

    def _validate_model(self, model: ast.ClassDef) -> None:
        for item in model.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                if item.value is not None and isinstance(item.value, ast.Call):
                    if _name(item.value.func) not in {"Field", "pydantic.Field"}:
                        raise UnsafeSchemaSnippet("Only Field(...) calls are allowed")
                elif item.value is not None:
                    _literal(item.value)
                continue
            if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                continue
            raise UnsafeSchemaSnippet(
                f"Model body statement is not allowed: {type(item).__name__}"
            )

    def _select_model(self) -> str | None:
        if self.target in self.models:
            return self.target
        for name in ("InputModel", "OutputModel"):
            if name in self.models:
                return name
        for suffix in ("Input", "Output", "Request", "Response"):
            matches = [name for name in self.models if name.endswith(suffix)]
            if matches:
                return matches[-1]
        return next(iter(self.models)) if len(self.models) == 1 else None

    def _model_schema(self, model: ast.ClassDef) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for item in model.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            field_name = item.target.id
            properties[field_name] = self._annotation_schema(item.annotation)
            is_required = item.value is None
            if isinstance(item.value, ast.Constant) and item.value.value is Ellipsis:
                is_required = True
            if isinstance(item.value, ast.Call):
                self._apply_field(item.value, properties[field_name])
                if item.value.args:
                    default = item.value.args[0]
                    is_required = isinstance(default, ast.Constant) and default.value is Ellipsis
                else:
                    is_required = not any(
                        keyword.arg == "default" for keyword in item.value.keywords
                    )
            if is_required:
                required.append(field_name)
        schema: dict[str, Any] = {
            "title": model.name,
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    def _apply_field(self, call: ast.Call, schema: dict[str, Any]) -> None:
        for keyword in call.keywords:
            mapped = _FIELD_CONSTRAINTS.get(keyword.arg or "")
            if mapped:
                schema[mapped] = _literal(keyword.value)

    def _annotation_schema(self, node: ast.expr) -> dict[str, Any]:
        annotation_name = _annotation_name(node)
        short_name = annotation_name.rsplit(".", 1)[-1] if annotation_name else None
        if short_name in _PRIMITIVES:
            return dict(_PRIMITIVES[short_name])
        if short_name in {"None", "NoneType"}:
            return {"type": "null"}
        if short_name in self.models:
            return {"$ref": f"#/$defs/{short_name}"}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return {"anyOf": [self._annotation_schema(node.left), self._annotation_schema(node.right)]}
        if isinstance(node, ast.Subscript):
            base = (_name(node.value) or "").rsplit(".", 1)[-1]
            args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            if base in {"Optional"}:
                return {"anyOf": [self._annotation_schema(args[0]), {"type": "null"}]}
            if base in {"Union"}:
                return {"anyOf": [self._annotation_schema(arg) for arg in args]}
            if base in {"Literal"}:
                values = [_literal(arg) for arg in args]
                schema: dict[str, Any] = {"enum": values}
                if values and all(isinstance(value, str) for value in values):
                    schema["type"] = "string"
                return schema
            if base in {"Annotated"}:
                return self._annotation_schema(args[0])
            if base in {"list", "List", "set", "Set", "tuple", "Tuple"}:
                return {
                    "type": "array",
                    "items": self._annotation_schema(args[0]) if args else {},
                }
            if base in {"dict", "Dict", "Mapping"}:
                return {
                    "type": "object",
                    "additionalProperties": self._annotation_schema(args[-1]) if args else {},
                }
        raise UnsafeSchemaSnippet(
            f"Unsupported annotation: {ast.unparse(node)[:120]}"
        )

    def _referenced_model_names(self, model: ast.ClassDef) -> set[str]:
        return {
            node.id
            for node in ast.walk(model)
            if isinstance(node, ast.Name) and node.id in self.models
        }


class PydanticCodeSchemaCompiler(SchemaCompilerPort):
    logger = structlog.get_logger()

    def to_json_schema(self, code: str) -> dict[str, Any]:
        if not code:
            return {}
        try:
            return _RestrictedSchemaParser(code).parse()
        except (SyntaxError, UnsafeSchemaSnippet) as exc:
            self.logger.warning(
                "Rejected connector schema snippet",
                error_type=type(exc).__name__,
            )
            return {}
