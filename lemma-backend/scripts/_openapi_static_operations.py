"""What the three static-operation generators have in common.

`generate_github_static_operations.py`, `generate_slack_static_operations.py`
and `generate_gmail_static_operations.py` all do the same three things: walk a
provider's own OpenAPI description, keep a hand-curated subset of it, and splice
the result into `lemma_apps_config.json` as that connector's
``static_operations``. Only the curation differs.

Shared here rather than copied, because the pruning in particular is asserted by
a guard test per connector. Three copies would drift, and the first sign of it
would be one connector's catalog entry quietly growing by an order of magnitude.

None of this is on the runtime import path. The generators are run by hand when
a curated set changes; what ships is the JSON they write.
"""

from __future__ import annotations

import json
from pathlib import Path

LEMMA_APPS_CONFIG_PATH = Path(__file__).parent / "lemma_apps_config.json"
SPECS_DIR = Path(__file__).parent.parent / "openapi_specs"

# Top-level property names and types, and nothing below that. Output schemas
# were 93% of the GitHub catalog entry -- `repos_get` alone was 72 KB, so one
# `describe_connector_operation` on it cost an agent roughly 18k tokens. What a
# model actually needs from an output schema is which fields come back; the
# shape of `owner.plan.collaborators` three levels down it can read off the
# response it already has.
_PROSE_KEYS = frozenset(
    {"description", "example", "examples", "title", "format", "default"}
)


def prune_output_schema(node: object, depth: int = 0) -> object:
    if not isinstance(node, dict):
        return node
    pruned: dict = {}
    for key, value in node.items():
        if key in _PROSE_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            pruned[key] = {
                name: {"type": sub.get("type")} if isinstance(sub, dict) else {}
                for name, sub in value.items()
            }
        elif key == "items":
            pruned[key] = prune_output_schema(value, depth + 1)
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
            pruned[key] = [prune_output_schema(value[0], depth + 1)] if value else []
        elif isinstance(value, dict):
            pruned[key] = prune_output_schema(value, depth + 1)
        else:
            pruned[key] = value
    return pruned


def operation_to_static_entry(op, extra_execution: dict | None = None) -> dict:
    """One `static_operations` entry, with the output schema pruned."""
    execution = {**(op.execution or {}), **(extra_execution or {})}
    entry: dict = {
        "name": op.public_name,
        "description": op.description,
        "execution": execution,
        "input_schema": op.input_schema,
    }
    if op.output_schema is not None:
        entry["output_schema"] = prune_output_schema(op.output_schema)
    return entry


def load_spec(name: str) -> dict:
    """Read a committed provider spec out of `lemma-backend/openapi_specs/`."""
    path = SPECS_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"Spec not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_into_lemma_apps_config(
    connector_id: str, static_operations: list[dict]
) -> None:
    apps = json.loads(LEMMA_APPS_CONFIG_PATH.read_text(encoding="utf-8"))
    for app in apps:
        if app.get("name") == connector_id:
            app["static_operations"] = static_operations
            break
    else:
        raise SystemExit(
            f"No '{connector_id}' entry found in lemma_apps_config.json — add the "
            "connector's non-operation fields (title, oauth2_config, "
            "system_oauth, ...) first, then re-run with --write."
        )
    LEMMA_APPS_CONFIG_PATH.write_text(
        json.dumps(apps, indent=2) + "\n", encoding="utf-8"
    )
