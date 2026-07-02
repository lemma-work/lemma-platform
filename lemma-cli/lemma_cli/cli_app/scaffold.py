"""Resource scaffolding for the init-centric build flow.

`lemma <resource> init <name>` writes a near-runnable, self-documenting bundle
file (JSONC — comments allowed, see pod_bundle.strip_jsonc) into the pod
bundle tree, with the backend defaults visible and editable. The human or
agent then edits the file and runs `lemma pods import`.

Templates use `__PLACEHOLDER__` tokens replaced via str.replace so JSON braces
stay literal (no f-string/`.format` escaping).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .enums import COLUMN_TYPES, SURFACE_PLATFORMS, TOOLSETS, VISIBILITY_VALUES
from .pod_bundle import FORMAT_VERSION, loads_jsonc, strip_jsonc


class ScaffoldError(Exception):
    """A scaffold could not be written (bad name, file exists without --force)."""


@dataclass
class ScaffoldResult:
    resource_type: str
    name: str
    files: list[Path]


# --------------------------------------------------------------------------- #
# name helpers
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    """Lowercase, hyphen-separated identifier (agents, workflows, surfaces)."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    if not slug:
        raise ScaffoldError(f"Cannot derive a resource name from {name!r}.")
    return slug


def snakeify(name: str) -> str:
    """snake_case identifier (functions, tables — must be valid SQL/Python)."""
    snake = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    if not snake or not re.match(r"^[a-z_][a-z0-9_]*$", snake):
        raise ScaffoldError(f"Cannot derive a code-safe name from {name!r}.")
    return snake


def pascalify(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^a-zA-Z0-9]+", name) if part) or "Item"


def _render(template: str, **tokens: str) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace(f"__{key}__", value)
    return out


def _fill_enums(template: str) -> str:
    """Fill the constant enum-list tokens in a template at import time, leaving
    the per-resource tokens (NAME, RLS, TABLE, …) for runtime `_render`. Keeps
    the scaffold comments in lockstep with the SDK enums (see enums.py)."""
    return _render(
        template,
        VISIBILITY=" | ".join(VISIBILITY_VALUES),
        COLUMN_TYPES=" ".join(COLUMN_TYPES),
        TOOLSETS=" ".join(TOOLSETS),
        SURFACE_PLATFORMS=" ".join(SURFACE_PLATFORMS),
    )


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #
POD_JSON = """{
  "format_version": __FORMAT_VERSION__,
  "name": "__NAME__",
  "description": "TODO: one line on what this pod does"
}
"""

TABLE_JSON = _fill_enums("""{
  // Folder name must equal "name". Import validates the match.
  "name": "__NAME__",
  "primary_key_column": "id",   // an auto UUID "id" is added; never declare id/created_at/updated_at/user_id
  "enable_rls": __RLS__,           // true = per-user private rows (default); false = shared team data
  "visibility": "POD",          // one of: __VISIBILITY__
  "columns": [
    { "name": "title", "type": "TEXT", "required": true, "max_length": 240 },
    { "name": "status", "type": "ENUM", "required": true, "default": "open",
      "options": ["open", "in_progress", "done"] }
    // types: __COLUMN_TYPES__
    // foreign key: { "name": "owner_id", "type": "UUID", "foreign_key": { "references": "people.id" } }
  ],
  "config": {}
}
""")

FUNCTION_JSON = _fill_enums("""{
  "name": "__NAME__",
  "description": "TODO: what this function does.",
  "type": "API",         // API = sync request/response; JOB = async background run
  "visibility": "POD",   // __VISIBILITY__
  "code": { "$file": "code.py" },
  "permissions": { "grants": [
    // Zero access by default — grant every table/folder/app this function touches.
    // { "resource_type": "datastore_table", "resource_name": "__TABLE__",
    //   "permission_ids": ["datastore.table.read", "datastore.record.write"] }
  ] }
}
""")

FUNCTION_CODE = """#input_type_name: __PASCAL__Input
#output_type_name: __PASCAL__Result
#function_name: __NAME__

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod


class __PASCAL__Input(BaseModel):
    # TODO: define the inputs this function takes.
    message: str


class __PASCAL__Result(BaseModel):
    ok: bool


async def __NAME__(ctx: FunctionContext, data: __PASCAL__Input) -> __PASCAL__Result:
    pod = Pod.from_env()  # authenticated as this function's workload principal
    # TODO: implement. e.g. pod.table("__TABLE__").create({...})
    return __PASCAL__Result(ok=True)
"""

AGENT_JSON = _fill_enums("""{
  "name": "__NAME__",
  "description": "TODO: what this agent decides or drafts.",
  "instruction": { "$file": "instruction.md" },
  // toolsets: __TOOLSETS__
  "toolsets": ["POD"],
  "visibility": "POD",   // __VISIBILITY__
  // agent_runtime omitted -> the system runtime profile (system:lemma). Add it to pin a specific model.
  "permissions": { "grants": [
    // Zero access by default — grant every table/folder/app this agent touches.
    // { "resource_type": "datastore_table", "resource_name": "__TABLE__",
    //   "permission_ids": ["datastore.table.read", "datastore.record.read"] }
  ] }
}
""")

AGENT_INSTRUCTION = """# __NAME__

You are **__NAME__**, an agent in this pod.

## Role and scope
TODO: one or two sentences on exactly what this agent is responsible for — and
what it is *not* responsible for. Keep it narrow.

## Pod resources you use
TODO: name the tables you read/write and the `/pod` folders that hold knowledge.
Agents do not discover these reliably on their own — spell them out.

## How to respond
TODO: describe the expected output. If another system consumes it, set
`output_schema` in the agent JSON and restate the fields here.

## Boundaries
TODO: what this agent must never do (e.g. "never message customers directly —
write a draft to a table and let a human approve it").
"""

WORKFLOW_JSON = """{
  "name": "__NAME__",
  "description": "TODO: what this workflow orchestrates.",
  "start": { "type": "MANUAL" },   // MANUAL | SCHEDULED | DATASTORE_EVENT | EVENT
  "nodes": [
    // Entry FORM collects the run input. Node types: FORM AGENT FUNCTION DECISION LOOP WAIT_UNTIL END
    { "id": "intake", "type": "FORM", "label": "Intake",
      "config": { "input_schema": {
        "type": "object",
        "properties": { "note": { "type": "string" } },
        "required": ["note"] } } },
    // Example next step (uncomment + create the agent, then wire an edge to it):
    // { "id": "process", "type": "AGENT", "label": "Process",
    //   "config": { "agent_name": "__AGENT__",
    //     "input_mapping": { "note": { "type": "expression", "value": "intake.note" } } } },
    { "id": "end", "type": "END", "label": "Done" }
  ],
  "edges": [
    { "id": "e1", "source": "intake", "target": "end" }
  ],
  "visibility": "POD"
}
"""

SCHEDULE_JSON = """{
  "name": "__NAME__",
  "schedule_type": "TIME",          // TIME (cron) | DATASTORE (row events) | WEBHOOK (app events)
  "config": { "cron": "0 9 * * *" },// TIME: cron; DATASTORE: {"datastore":"<table>","operations":["INSERT"]}
  // Exactly one target — the agent or workflow to start. It must already exist in the pod.
  "workflow_name": "__TARGET__",
  // "agent_name": "__TARGET__",
  "is_active": false,               // flip to true once the target exists and you've imported it
  "visibility": "POD"
}
"""

SURFACE_JSON = _fill_enums("""{
  "name": "__PLATFORM_LOWER__",
  "platform": "__PLATFORM__",          // __SURFACE_PLATFORMS__
  "default_agent_name": "__AGENT__",   // the pod agent that answers on this surface
  "credential_mode": "CUSTOM",         // SYSTEM (Lemma-managed) | CUSTOM (your own connector account)
  "account_id": "TODO-connector-account-uuid",  // required for CUSTOM; set up with `lemma connectors`
  "is_enabled": false                  // flip to true once the agent + account exist
}
""")

FOLDER_JSON = _fill_enums("""{
  "description": "TODO: what this folder holds.",
  "visibility": "POD"   // __VISIBILITY__
}
""")

README_MD = """# __NAME__

TODO: what this pod does and who operates it.

## Build loop
```bash
lemma pods import ./__NAME__ --dry-run   # validate
lemma pods import ./__NAME__             # upsert by resource name
```

## Non-bundled setup (do these after import)
- Upload any required files: `lemma files upload ./doc.pdf /pod/knowledge/doc.pdf`
- Connect connectors/accounts: `lemma connectors ...`
- Flip schedules/surfaces to active once their targets exist.

## Verify
```bash
lemma pods describe
lemma agents chat hello "what can you do in this pod?"
```
"""

AGENTS_MD = """# Building this Lemma pod

This directory is a **pod bundle**: plain files imported with `lemma pods import`.
The bundle is the source of truth. Edit files, then re-import.

## Layout (folder name MUST equal each resource's `name`)
```
__NAME__/
  pod.json
  tables/<name>/<name>.json
  functions/<name>/<name>.json + code.py     # JSON carries permissions.grants
  agents/<name>/<name>.json    + instruction.md
  workflows/<name>/<name>.json
  schedules/<name>/<name>.json
  surfaces/<platform>/<platform>.json
  files/<folder>/.folder.json
```

## Scaffold more resources (then edit them)
```bash
lemma table init <name>        # tables/<name>/<name>.json
lemma function init <name>     # functions/<name>/<name>.json + code.py
lemma agent init <name>        # agents/<name>/<name>.json + instruction.md
lemma workflow init <name>     # workflows/<name>/<name>.json
```

## Rules that bite
- **Zero access by default.** Grant every table/folder/app an agent or function
  touches via `permissions.grants` in its JSON.
- Files referenced as `{"$file": "code.py"}` / `{"$json_file": "config.json"}`
  load from sibling files. Bundle JSON may contain `//` and `/* */` comments.
- Build order: tables -> files -> functions -> agents -> workflows -> schedules
  -> surfaces -> apps. Verify each layer before the next.
- `lemma pods import . --dry-run` validates everything without writing.
"""


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def _write(path: Path, content: str, *, force: bool) -> Path:
    if path.exists() and not force:
        raise ScaffoldError(f"{path} already exists (use --force to overwrite).")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def find_bundle_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a pod.json — the bundle root."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pod.json").is_file():
            return candidate
    return None


def resolve_root(explicit_root: Path | None) -> Path:
    """Where a single-resource scaffold should write into. Prefers an explicit
    --root, then the enclosing bundle (pod.json found upward), then cwd."""
    if explicit_root is not None:
        return explicit_root
    return find_bundle_root(Path.cwd()) or Path.cwd()


def init_pod(
    directory: Path, name: str, *, force: bool = False, with_starter: bool = True
) -> ScaffoldResult:
    """Scaffold an importable pod bundle: pod.json, README, and AGENTS.md.

    With ``with_starter`` (the default) it also writes a shared ``items`` table
    and a starter ``hello`` agent granted to it. Pass ``with_starter=False`` (CLI
    ``--no-starter``) for a bare bundle when you already know your resource names
    and don't want to delete generic scaffolding first."""
    slug = slugify(name)
    files: list[Path] = []
    files.append(_write(directory / "pod.json", _render(POD_JSON, NAME=slug, FORMAT_VERSION=str(FORMAT_VERSION)), force=force))

    if with_starter:
        # A shared "items" table.
        table_name = "items"
        files.append(
            _write(
                directory / "tables" / table_name / f"{table_name}.json",
                _render(TABLE_JSON, NAME=table_name, RLS="false"),
                force=force,
            )
        )

        # A starter agent granted read/write on the table. Named "hello" so it
        # reads as an example to rename, never confused with the pod's native
        # assistant.
        agent_name = "hello"
        agent_json = _render(STARTER_AGENT_JSON, NAME=agent_name, TABLE=table_name)
        files.append(_write(directory / "agents" / agent_name / f"{agent_name}.json", agent_json, force=force))
        files.append(
            _write(
                directory / "agents" / agent_name / "instruction.md",
                _render(STARTER_AGENT_INSTRUCTION, NAME=agent_name, TABLE=table_name),
                force=force,
            )
        )

    files.append(_write(directory / "README.md", _render(README_MD, NAME=slug), force=force))
    files.append(_write(directory / "AGENTS.md", _render(AGENTS_MD, NAME=slug), force=force))
    return ScaffoldResult("pod", slug, files)


STARTER_AGENT_JSON = """{
  "name": "__NAME__",
  "description": "Starter agent (rename me) that reads and writes the __TABLE__ table.",
  "instruction": { "$file": "instruction.md" },
  "toolsets": ["POD"],
  "visibility": "POD",
  "permissions": { "grants": [
    { "resource_type": "datastore_table", "resource_name": "__TABLE__",
      "permission_ids": ["datastore.table.read", "datastore.record.read", "datastore.record.write"] }
  ] }
}
"""

STARTER_AGENT_INSTRUCTION = """# __NAME__

You are **__NAME__**, a starter agent in this pod — rename me to a one-word job
title (`scout`, `triage`, `chaser`) once you know the job this pod does.

## Role
Help the operator capture and track work in the `__TABLE__` table. Each row has
a `title` and a `status` (open / in_progress / done).

## What you can do
- Read and query the `__TABLE__` table to answer questions about open work.
- Create rows when the operator describes a new item.
- Update a row's `status` as work progresses.

## Boundaries
Confirm before deleting anything. Keep durable state in the table, not in chat.
"""


def report(result: ScaffoldResult, *, next_hint: str | None = None) -> None:
    """Print a scaffold result (deferred console import keeps cli_app below cli_core)."""
    from ..cli_core.state import console

    console.print(f"[green]init[/green] {result.resource_type} [bold]{result.name}[/bold]")
    for path in result.files:
        console.print(f"  [dim]wrote[/dim] {path}")
    if next_hint:
        console.print(f"[dim]next:[/dim] {next_hint}")


def init_resource(
    resource_type: str,
    name: str,
    *,
    root: Path | None = None,
    force: bool = False,
    shared: bool = False,
    platform: str | None = None,
    runtime: str | None = None,
) -> ScaffoldResult:
    """Scaffold one resource's file(s) into the bundle tree under `root`."""
    base = resolve_root(root)

    if resource_type in {"table", "tables"}:
        slug = snakeify(name)
        path = base / "tables" / slug / f"{slug}.json"
        files = [_write(path, _render(TABLE_JSON, NAME=slug, RLS="false" if shared else "true"), force=force)]
        return ScaffoldResult("table", slug, files)

    if resource_type in {"function", "functions"}:
        slug = snakeify(name)
        pascal = pascalify(name)
        rdir = base / "functions" / slug
        files = [
            _write(rdir / f"{slug}.json", _render(FUNCTION_JSON, NAME=slug, TABLE="items"), force=force),
            _write(rdir / "code.py", _render(FUNCTION_CODE, NAME=slug, PASCAL=pascal, TABLE="items"), force=force),
        ]
        return ScaffoldResult("function", slug, files)

    if resource_type in {"agent", "agents"}:
        slug = slugify(name)
        rdir = base / "agents" / slug
        agent_json = _render(AGENT_JSON, NAME=slug, TABLE="items")
        if runtime:
            agent_json = agent_json.replace(
                "  // agent_runtime omitted -> the system runtime profile (system:lemma). "
                "Add it to pin a specific model.",
                f'  "agent_runtime": {{ "profile_id": "{runtime}" }},',
            )
        files = [
            _write(rdir / f"{slug}.json", agent_json, force=force),
            _write(rdir / "instruction.md", _render(AGENT_INSTRUCTION, NAME=slug), force=force),
        ]
        return ScaffoldResult("agent", slug, files)

    if resource_type in {"workflow", "workflows"}:
        slug = slugify(name)
        path = base / "workflows" / slug / f"{slug}.json"
        files = [_write(path, _render(WORKFLOW_JSON, NAME=slug, AGENT="some-agent"), force=force)]
        return ScaffoldResult("workflow", slug, files)

    if resource_type in {"schedule", "schedules"}:
        slug = slugify(name)
        path = base / "schedules" / slug / f"{slug}.json"
        files = [_write(path, _render(SCHEDULE_JSON, NAME=slug, TARGET="some-workflow"), force=force)]
        return ScaffoldResult("schedule", slug, files)

    if resource_type in {"surface", "surfaces"}:
        plat = (platform or name).strip().upper()
        plat_lower = plat.lower()
        path = base / "surfaces" / plat_lower / f"{plat_lower}.json"
        files = [
            _write(
                path,
                _render(SURFACE_JSON, PLATFORM=plat, PLATFORM_LOWER=plat_lower, AGENT="some-agent"),
                force=force,
            )
        ]
        return ScaffoldResult("surface", plat_lower, files)

    if resource_type in {"file", "files", "folder"}:
        folder = snakeify(name)
        path = base / "files" / folder / ".folder.json"
        files = [_write(path, FOLDER_JSON, force=force)]
        return ScaffoldResult("folder", folder, files)

    raise ScaffoldError(f"Unknown resource type for init: {resource_type!r}")


# --------------------------------------------------------------------------- #
# schema / example printing (X10)
# --------------------------------------------------------------------------- #
def resource_example(resource_type: str, name: str = "example") -> str:
    """The JSONC scaffold body for a resource type — the canonical 'what fields
    exist' reference printed by `lemma <resource> schema`."""
    rt = resource_type.lower().rstrip("s")
    if rt == "pod":
        return _render(POD_JSON, NAME=slugify(name), FORMAT_VERSION=str(FORMAT_VERSION))
    if rt == "table":
        return _render(TABLE_JSON, NAME=snakeify(name), RLS="true")
    if rt == "function":
        return _render(FUNCTION_JSON, NAME=snakeify(name), TABLE="items")
    if rt == "agent":
        return _render(AGENT_JSON, NAME=slugify(name), TABLE="items")
    if rt == "workflow":
        return _render(WORKFLOW_JSON, NAME=slugify(name), AGENT="some-agent")
    if rt == "schedule":
        return _render(SCHEDULE_JSON, NAME=slugify(name), TARGET="some-workflow")
    if rt == "surface":
        return _render(SURFACE_JSON, PLATFORM="SLACK", PLATFORM_LOWER="slack", AGENT="some-agent")
    raise ScaffoldError(f"No example for resource type: {resource_type!r}")


# --------------------------------------------------------------------------- #
# grants (X7/G1)
# --------------------------------------------------------------------------- #
PERMISSION_PRESETS: dict[str, dict[str, list[str]]] = {
    "datastore_table": {
        "read": ["datastore.table.read", "datastore.record.read"],
        # record deletion rides on datastore.record.write; there is no
        # datastore.record.delete permission in the backend registry.
        "write": ["datastore.record.write"],
        # Structural table deletion — a DESTRUCTIVE action: granting it gives
        # the workload standing authority (no runtime approval prompt).
        "delete": ["datastore.table.delete"],
    },
    "folder": {
        "read": ["folder.read"],
        "write": ["folder.write"],
    },
    "connector": {
        "use": ["connector.use"],
    },
    # Compose functions/agents as tools on an agent/function. Execute is
    # self-sufficient — granting it lets the parent both load and run the tool
    # (the backend no longer also requires function.read). One token per verb:
    # `execute` here (apps use `use`, matching connector.use).
    "function": {
        "execute": ["function.execute"],
        "read": ["function.read"],
    },
    "agent": {
        "execute": ["agent.execute"],
    },
}
_GRANT_TYPE_ALIASES = {
    "table": "datastore_table",
    "datastore_table": "datastore_table",
    "folder": "folder",
    "file": "folder",
    "app": "connector",
    "connector": "connector",
    "function": "function",
    "agent": "agent",
}


def parse_grant_spec(spec: str) -> dict:
    """Parse `name:perms` or `type:name:perms` into a grant dict.

    `name:perms`  — type inferred (a leading `/` means a folder, else a table).
    `type:name:perms` — explicit type (table | folder | app).
    `perms` is comma-separated friendly tokens (read/write/delete/use) or raw
    permission ids (e.g. `datastore.record.write`)."""
    parts = spec.split(":")
    if len(parts) == 3:
        type_token, name, perms = parts
        rtype = _GRANT_TYPE_ALIASES.get(type_token.strip().lower())
        if not rtype:
            raise ScaffoldError(
                f"Unknown grant type {type_token!r}. "
                "Use table, folder, app, function, or agent."
            )
    elif len(parts) == 2:
        name, perms = parts
        rtype = "folder" if name.startswith("/") else "datastore_table"
    else:
        raise ScaffoldError(
            f"Bad grant {spec!r}. Use name:perms (e.g. tickets:read,write) "
            "or type:name:perms (e.g. app:gmail:use)."
        )
    name = name.strip()
    presets = PERMISSION_PRESETS[rtype]
    perm_ids: list[str] = []
    for token in perms.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token in presets:
            perm_ids.extend(presets[token])
        elif "." in token:  # a raw permission id, e.g. folder.read / datastore.record.write
            perm_ids.append(token)
        else:
            raise ScaffoldError(
                f"Unknown permission {token!r} for {rtype}. Try: {', '.join(presets)}."
            )
    deduped: list[str] = []
    for pid in perm_ids:
        if pid not in deduped:
            deduped.append(pid)
    return {"resource_type": rtype, "resource_name": name, "permission_ids": deduped}


def merge_grants(existing: list[dict], new: list[dict]) -> list[dict]:
    """Union grants by (resource_type, resource_name), merging permission ids."""
    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for grant in [*existing, *new]:
        key = (grant.get("resource_type"), grant.get("resource_name"))
        if key not in merged:
            merged[key] = {
                "resource_type": grant.get("resource_type"),
                "resource_name": grant.get("resource_name"),
                "permission_ids": [],
            }
            order.append(key)
        for pid in grant.get("permission_ids") or []:
            if pid not in merged[key]["permission_ids"]:
                merged[key]["permission_ids"].append(pid)
    return [merged[key] for key in order]


# The `start` namespace only ever exposes these sub-keys (see the backend's
# workflow RunContext / TriggerContext). `start.inputs.*` and bare `start.<x>`
# are a common authoring mistake that JMESPath silently resolves to null.
_VALID_START_KEYS = {"payload", "metadata", "llm_output"}
_START_REF = re.compile(r"\bstart\.([A-Za-z_][A-Za-z0-9_]*)")

# Required `start.config` shape per non-MANUAL start type. The backend models a
# discriminated union on `start.type` where every non-manual variant declares a
# *required* typed `config` (lemma-backend app/modules/workflow/api/schemas.py
# WorkflowStartInput, lines 67-114), so the server 422s when `config` is missing
# or missing an inner required field. MANUAL must have config null/omitted.
# Required inner fields come from the domain models in
# lemma-backend app/modules/workflow/domain/start.py:
#   SCHEDULED       -> ScheduledFlowStart.schedule_type   (ONCE | CRON)   (l.51-58)
#   EVENT           -> EventFlowStart.connector_trigger_id + connector_id (l.61-70)
#   DATASTORE_EVENT -> DataStoreFlowStart.table_name        (l.26-37)
_START_CONFIG_REQUIRED: dict[str, list[str]] = {
    "SCHEDULED": ["schedule_type"],
    "EVENT": ["connector_trigger_id", "connector_id"],
    "DATASTORE_EVENT": ["table_name"],
}
# Allowed enum values for inner config fields the server further constrains.
_SCHEDULE_TYPES = {"ONCE", "CRON"}
_DATASTORE_OPERATIONS = {"INSERT", "UPDATE", "DELETE"}


def _validate_start(payload: dict) -> list[str]:
    """Flag a `start` block the backend would 422 on: a non-MANUAL start type
    whose required `start.config` is missing or malformed (mirrors the
    discriminated WorkflowStartInput union and the FlowStart domain models)."""
    issues: list[str] = []
    start = payload.get("start")
    if not isinstance(start, dict):
        return issues  # no start -> server treats as MANUAL; nothing to require
    stype = str(start.get("type") or "MANUAL").upper()
    if stype == "MANUAL":
        return issues
    required = _START_CONFIG_REQUIRED.get(stype)
    if required is None:
        return issues  # unknown type; node/graph checks aren't the place to gate it
    cfg = start.get("config")
    if not isinstance(cfg, dict):
        issues.append(
            f"start.type is {stype} but start.config is missing — the server "
            f"requires a config with {', '.join(required)} (a MANUAL workflow "
            "needs no config; non-manual triggers 422 without one)."
        )
        return issues
    for field in required:
        value = cfg.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(
                f"{stype} start.config is missing required field "
                f"`{field}` (the server 422s without it)."
            )
    if stype == "SCHEDULED":
        schedule_type = cfg.get("schedule_type")
        if isinstance(schedule_type, str) and schedule_type.upper() not in _SCHEDULE_TYPES:
            issues.append(
                f"SCHEDULED start.config.schedule_type '{schedule_type}' is "
                f"invalid — use one of {', '.join(sorted(_SCHEDULE_TYPES))}."
            )
    if stype == "DATASTORE_EVENT":
        operations = cfg.get("operations")
        if isinstance(operations, list):
            bad = [
                op
                for op in operations
                if not (isinstance(op, str) and op.strip().upper() in _DATASTORE_OPERATIONS)
            ]
            if bad:
                issues.append(
                    "DATASTORE_EVENT start.config.operations has invalid entries "
                    f"{bad} — use only INSERT, UPDATE, DELETE."
                )
    return issues


def _decision_misroute_issues(
    nodes: list, edges: list
) -> list[str]:
    """Flag DECISION nodes whose outgoing-edge shape risks the silent-misroute the
    dogfood hit: a decision routes a non-matching case (e.g. a rejection) to the
    first-listed outgoing edge, which can be a 'positive' branch.

    Backend routing (lemma-backend app/modules/workflow/execution/executors/
    decision.py:9-20 + domain/flow.py:69-78): the first rule whose condition is
    truthy selects `rule.next_node_id`; if NO rule matches, the run falls through
    to the single default outgoing *edge* (`next_after` = first outgoing edge).
    So an unhandled input lands on that default edge silently — no error.

    We warn when a DECISION has rule(s) AND a fall-through outgoing edge, because
    the no-match case is then routed implicitly. The fix is an explicit else:
    cover every case with a rule (and let the run end), or make the fall-through
    edge's intent obvious. We name the rule that the default would mis-route."""
    issues: list[str] = []
    outgoing: dict[str, list] = {}
    for edge in edges:
        src = str(edge.get("source") or "")
        outgoing.setdefault(src, []).append(edge)
    for node in nodes:
        if str(node.get("type") or "").upper() != "DECISION":
            continue
        nid = node.get("id")
        cfg = node.get("config") or {}
        rules = cfg.get("rules") or []
        rule_targets = {
            str(r.get("next_node_id"))
            for r in rules
            if isinstance(r, dict) and r.get("next_node_id")
        }
        node_edges = outgoing.get(str(nid), [])
        if not rules:
            issues.append(
                f"DECISION node '{nid}' has no rules — every run falls straight "
                "through to its default edge. Add condition rules or use a plain node."
            )
            continue
        if not node_edges:
            # No fall-through: a non-matching input dead-ends (run stops). That is
            # a different footgun, but not the silent-misroute; still worth a nudge.
            issues.append(
                f"DECISION node '{nid}' has rules but no default outgoing edge — "
                "an input matching no rule stops the run with no next node. Add a "
                "catch-all edge (the explicit else) so the no-match path is intentional."
            )
            continue
        default_target = str(node_edges[0].get("target") or "")
        # The dangerous, hard-to-see case: the implicit default lands on a node
        # that a *rule* also targets (so 'no match' silently behaves like a
        # specific rule, e.g. routing a rejection into the approve branch).
        if default_target in rule_targets:
            culprit = next(
                (
                    str(r.get("condition"))
                    for r in rules
                    if isinstance(r, dict) and str(r.get("next_node_id")) == default_target
                ),
                "?",
            )
            issues.append(
                f"DECISION node '{nid}' silently routes unmatched inputs to its "
                f"default edge -> '{default_target}', the same node rule "
                f"`{culprit}` targets. A case that matches no rule would be treated "
                "like that rule fired. Make the else explicit: add a rule for the "
                "remaining case, or point the default edge at a distinct handler."
            )
        elif len(node_edges) == 1:
            # Single fall-through edge to a node no rule names: the no-match path
            # is implicit. Lower severity, but flag it so the author confirms it.
            issues.append(
                f"DECISION node '{nid}' routes every no-match input to its only "
                f"outgoing edge -> '{default_target}' implicitly. Confirm this is "
                "the intended else; an unhandled case will land here without warning."
            )
    return issues


def _iter_expressions(node: object):
    """Yield expression/condition strings from anywhere in a workflow graph."""
    if isinstance(node, dict):
        if str(node.get("type") or "").lower() == "expression" and isinstance(
            node.get("value"), str
        ):
            yield node["value"]
        if isinstance(node.get("condition"), str):
            yield node["condition"]
        for value in node.values():
            yield from _iter_expressions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_expressions(item)


def validate_workflow(payload: dict) -> list[str]:
    """Static structural checks on a workflow graph, before import: node ids,
    edge endpoints, a single entry, an END node, and AGENT/FUNCTION targets."""
    issues: list[str] = []
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    ids: list[str] = [str(n.get("id")) for n in nodes if n.get("id")]
    id_set = set(ids)

    # Catch the silent-null footgun: expressions reading an unknown `start.*`
    # sub-namespace. Only payload/metadata/llm_output exist; trigger data is
    # under `start.payload.*` and manual runs have no `start` at all.
    bad_start: set[str] = set()
    for expr in _iter_expressions(payload):
        for seg in _START_REF.findall(expr):
            if seg not in _VALID_START_KEYS:
                bad_start.add(seg)
    for seg in sorted(bad_start):
        issues.append(
            f"expression references `start.{seg}` — the only valid `start` "
            "sub-namespaces are payload, metadata, llm_output. Trigger data is "
            "at `start.payload.*` (manual runs have no `start`)."
        )

    # Non-MANUAL start types need a well-formed `start.config` or the server 422s.
    issues.extend(_validate_start(payload))

    if not nodes:
        issues.append("workflow has no nodes.")
        return issues
    for dup in {i for i in ids if ids.count(i) > 1}:
        issues.append(f"duplicate node id '{dup}'.")

    targeted: set[str] = set()
    for edge in edges:
        src, dst = str(edge.get("source") or ""), str(edge.get("target") or "")
        if src not in id_set:
            issues.append(f"edge source '{src}' is not a node id.")
        if dst not in id_set:
            issues.append(f"edge target '{dst}' is not a node id.")
        targeted.add(dst)

    entries = [i for i in id_set if i not in targeted]
    if len(entries) == 0:
        issues.append("no entry node (every node has an incoming edge — cycle?).")
    elif len(entries) > 1:
        issues.append(f"multiple entry nodes: {', '.join(sorted(entries))}.")

    if not any(str(n.get("type")).upper() == "END" for n in nodes):
        issues.append("no END node.")

    for node in nodes:
        ntype = str(node.get("type")).upper()
        cfg = node.get("config") or {}
        if ntype == "AGENT" and not cfg.get("agent_name"):
            issues.append(f"AGENT node '{node.get('id')}' has no config.agent_name.")
        if ntype == "FUNCTION" and not cfg.get("function_name"):
            issues.append(f"FUNCTION node '{node.get('id')}' has no config.function_name.")

    # DECISION nodes whose rule/edge shape risks routing an unmatched input
    # (e.g. a rejection) silently onto a 'positive' default branch.
    issues.extend(_decision_misroute_issues(nodes, edges))
    return issues


# POD_MEMBER_TOKEN (the legacy pod-member placeholder) is defined in pod_bundle
# and imported above. Member/account ids are turned into ${name} variables on
# export by pod_bundle._extract_portable_variables; substitute_placeholders below
# still resolves the legacy token on import for older bundles.


def substitute_placeholders(node: object, replacements: dict[str, str]) -> object:
    """Return a copy of ``node`` with any string equal to a placeholder key
    replaced by its value. Used at import time to resolve POD_MEMBER_TOKEN (and
    any future tokens) to concrete ids."""
    if isinstance(node, dict):
        return {k: substitute_placeholders(v, replacements) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute_placeholders(v, replacements) for v in node]
    if isinstance(node, str) and node in replacements:
        return replacements[node]
    return node


def templatize_bundle(output_dir: Path) -> tuple[Path, int]:
    """Strip the remaining instance-specific data from an exported bundle so it
    can seed new pods: the pinned agent runtimes. (Non-portable member/account
    ids are already turned into ``${name}`` variables on every export, so only
    the runtime pin is template-specific.) Returns (bundle_root, files_changed)."""
    root = output_dir
    if not (root / "pod.json").is_file():
        for child in sorted(output_dir.iterdir()) if output_dir.is_dir() else []:
            if child.is_dir() and (child / "pod.json").is_file():
                root = child
                break

    changed = 0

    def _strip(path: Path, key: str) -> None:
        nonlocal changed
        data = loads_jsonc(path.read_text(encoding="utf-8"))
        if data.pop(key, None) is not None:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            changed += 1

    for agent_json in sorted((root / "agents").glob("*/*.json")):
        _strip(agent_json, "agent_runtime")
    return root, changed


def _find_grants_array_span(text: str) -> tuple[int, int] | None:
    """Return the [start, end) offsets of the JSON array that is the value of a
    `"grants"` key in `text`, or None if it can't be located.

    Comments are blanked via `strip_jsonc` (which preserves byte offsets) before
    scanning, so a `]` inside a comment never trips the bracket match. The span
    found in the blanked text maps 1:1 onto the original."""
    blanked = strip_jsonc(text)
    key = re.search(r'"grants"\s*:', blanked)
    if not key:
        return None
    i = blanked.find("[", key.end())
    if i == -1:
        return None
    start = i
    depth = 0
    in_string = False
    escaped = False
    while i < len(blanked):
        ch = blanked[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    return None


def splice_grants(original_text: str, merged_grants: list[dict]) -> str | None:
    """Replace only the `"grants"` array in `original_text` with `merged_grants`,
    preserving every comment outside that array. Returns None if the array can't
    be located (caller should fall back to a full rewrite)."""
    span = _find_grants_array_span(original_text)
    if span is None:
        return None
    start, end = span
    # Indent the array to sit under the existing `"grants":` key.
    line_start = original_text.rfind("\n", 0, start) + 1
    indent = original_text[line_start:start]
    indent = indent[: len(indent) - len(indent.lstrip())]
    serialized = json.dumps(merged_grants, indent=2)
    serialized = serialized.replace("\n", "\n" + indent)
    return original_text[:start] + serialized + original_text[end:]


def grant_in_bundle(resource_type: str, name: str, specs: list[str], *, root: Path | None = None) -> tuple[Path, dict]:
    """Merge grants into a resource's bundle JSON, in place.

    The `"grants"` array is spliced so the scaffold's surrounding comments
    (visibility, toolsets, runtime guidance) survive. Only if the array can't be
    located do we fall back to a full plain-JSON rewrite (comments dropped)."""
    base = resolve_root(root)
    folder = "agents" if resource_type in {"agent", "agents"} else "functions"
    slug = slugify(name) if folder == "agents" else snakeify(name)
    json_path = base / folder / slug / f"{slug}.json"
    if not json_path.is_file():
        raise ScaffoldError(
            f"{json_path} not found — run `lemma {resource_type} init {name}` first."
        )
    original = json_path.read_text(encoding="utf-8")
    payload = loads_jsonc(original)
    existing = (payload.get("permissions") or {}).get("grants") or []
    new = [parse_grant_spec(spec) for spec in specs]
    merged = merge_grants(existing, new)

    spliced = splice_grants(original, merged) if payload.get("permissions") else None
    if spliced is not None:
        json_path.write_text(spliced, encoding="utf-8")
    else:
        payload["permissions"] = {"grants": merged}
        json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return json_path, {"grants": merged}
