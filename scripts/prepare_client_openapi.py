#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _operation_is_excluded(operation: Mapping[str, Any], path: str, excluded_tags: set[str], excluded_prefixes: tuple[str, ...]) -> bool:
    tags = {str(tag).lower() for tag in operation.get("tags", [])}
    if tags & excluded_tags:
        return True
    return path.startswith(excluded_prefixes)


def _collect_schema_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            refs.add(ref.rsplit("/", 1)[-1])
        for item in value.values():
            _collect_schema_refs(item, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_schema_refs(item, refs)


def _retain_referenced_schemas(schema: dict[str, Any]) -> None:
    components = schema.get("components")
    if not isinstance(components, dict):
        return
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return

    referenced: set[str] = set()
    _collect_schema_refs(schema.get("paths", {}), referenced)

    changed = True
    while changed:
        changed = False
        for name in list(referenced):
            model = schemas.get(name)
            before = len(referenced)
            _collect_schema_refs(model, referenced)
            changed = changed or len(referenced) != before

    for name in list(schemas):
        if name not in referenced:
            schemas.pop(name, None)


def prepare_client_openapi(
    schema: dict[str, Any],
    *,
    excluded_tags: set[str],
    excluded_prefixes: tuple[str, ...],
    prune_unreferenced_schemas: bool,
) -> dict[str, Any]:
    paths = schema.get("paths")
    if isinstance(paths, dict):
        for path in list(paths):
            path_item = paths[path]
            if not isinstance(path_item, dict):
                continue
            for method in list(path_item):
                if method.lower() not in HTTP_METHODS:
                    continue
                operation = path_item[method]
                if isinstance(operation, Mapping) and _operation_is_excluded(
                    operation,
                    path,
                    excluded_tags,
                    excluded_prefixes,
                ):
                    path_item.pop(method, None)
            if not any(method.lower() in HTTP_METHODS for method in path_item):
                paths.pop(path, None)

    if prune_unreferenced_schemas:
        _retain_referenced_schemas(schema)

    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Lemma OpenAPI for public SDK generation.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--exclude-tag",
        action="append",
        default=[],
        help="OpenAPI tag to exclude. Case-insensitive. Can be provided multiple times.",
    )
    parser.add_argument(
        "--exclude-prefix",
        action="append",
        default=[],
        help="Path prefix to exclude. Can be provided multiple times.",
    )
    parser.add_argument(
        "--keep-unreferenced-schemas",
        action="store_true",
        help="Keep component schemas that are no longer referenced after endpoint filtering.",
    )
    args = parser.parse_args()

    schema = json.loads(args.input.read_text(encoding="utf-8"))
    prepared = prepare_client_openapi(
        schema,
        excluded_tags={tag.lower() for tag in args.exclude_tag},
        excluded_prefixes=tuple(args.exclude_prefix),
        prune_unreferenced_schemas=not args.keep_unreferenced_schemas,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(prepared, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
