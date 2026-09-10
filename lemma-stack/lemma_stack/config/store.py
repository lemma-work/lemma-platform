"""The one user-facing config file: ~/.lemma/local/config.toml.

lemma-stack owns this file (tomlkit preserves user comments on rewrite). All
rendered env files under run/ are derived from it and regenerated on every
start/upgrade — users edit config.toml, never the rendered output.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit import TOMLDocument

from lemma_stack.output import AdminError
from lemma_stack.paths import LocalPaths

SCHEMA_VERSION = 1
SECRET_KEY_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)

DEFAULT_PORTS = {
    "frontend": 3711,
    "backend": 8711,
    # Transitional loopback forwards used only while locald host packs run
    # against Docker/Podman-managed private infrastructure.
    "postgres": 55432,
    "redis": 56379,
    "supertokens": 53567,
}

_TEMPLATE = """\
# Lemma local stack configuration — owned by lemma-stack.
# Edit values here, then run `lemma-stack restart` to apply.
# Rendered env files under run/ are generated from this file; do not edit them.

schema = 1

[install]
channel = "stable"
mode = "images"

[runtime]
provider = "podman"

[ports]
frontend = 3711
backend = 8711
postgres = 55432
redis = 56379
supertokens = 53567

[features]
observability = false

# Extra backend settings (UPPER_SNAKE env vars), passed to the backend
# container verbatim. Example: LEMMA_ANTHROPIC_API_KEY = "sk-ant-..."
[backend.env]

[frontend.env]

# Generated values — do not edit.
[internal]
"""


def new_document() -> TOMLDocument:
    doc = tomlkit.parse(_TEMPLATE)
    doc["internal"]["installation_secret"] = secrets.token_hex(16)
    return doc


def document_schema(doc: TOMLDocument) -> int:
    """The schema this document was written against.

    Absent means 1. The template has always written `schema = 1`, so a file
    without it is either hand-made or predates the field, and in both cases
    version 1 is what its contents mean.

    A value that is not an integer is a lie about the format, not a version, so
    it is refused rather than coerced.
    """
    declared = doc.get("schema", 1)
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise AdminError(
            f"config has schema = {declared!r}, which is not a version number. "
            f"Fix or remove the line."
        )
    return declared


def migrate(doc: TOMLDocument, from_schema: int) -> TOMLDocument:
    """Bring a document written against an older schema up to this one.

    There is one version, so there is nothing to do yet -- and the seam is the
    point. A version 2 arriving with no place to put its migration is how a
    config file gets read as something it is not, and `load` below is the only
    caller, so a migration written here reaches every command at once.
    """
    for step in range(from_schema, SCHEMA_VERSION):
        raise AdminError(  # pragma: no cover -- no step exists to exercise
            f"no migration from config schema {step} to {step + 1}"
        )
    doc["schema"] = SCHEMA_VERSION
    return doc


def load(paths: LocalPaths) -> TOMLDocument:
    if not paths.config_file.exists():
        raise AdminError(f"no config found at {paths.config_file}; run `lemma-stack install` first")
    doc = tomlkit.parse(paths.config_file.read_text(encoding="utf-8"))
    # Read, not just written. `SCHEMA_VERSION` was declared here and the
    # template wrote `schema = 1`, and nothing ever compared them -- so a config
    # written by a newer lemma-stack was parsed as if it were this one, and
    # whatever that version meant by a field was silently misread. This is the
    # same shape as an old app opening new data, which is the one thing a local
    # install must refuse rather than guess at.
    schema = document_schema(doc)
    if schema > SCHEMA_VERSION:
        raise AdminError(
            f"{paths.config_file} was written by a newer Lemma (config schema "
            f"{schema}; this one understands {SCHEMA_VERSION}). Upgrade "
            f"lemma-stack rather than editing the file: the settings it holds "
            f"are not lost, they are just described in a way this version does "
            f"not read."
        )
    if schema < SCHEMA_VERSION:
        return migrate(doc, schema)
    return doc


def load_or_create(paths: LocalPaths) -> TOMLDocument:
    if paths.config_file.exists():
        return load(paths)
    doc = new_document()
    save(paths, doc)
    return doc


def save(paths: LocalPaths, doc: TOMLDocument) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(tomlkit.dumps(doc), encoding="utf-8")
    paths.config_file.chmod(0o600)


def _is_env_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", key))


def resolve_key(key: str) -> tuple[str, ...]:
    """Map a CLI key to its location in the document.

    Bare UPPER_SNAKE keys route to [backend.env] so
    `lemma-stack config set LEMMA_OPENAI_API_KEY sk-...` just works;
    dotted keys address sections directly (`ports.frontend`,
    `frontend.env.FOO`).
    """
    if _is_env_key(key):
        return ("backend", "env", key)
    parts = tuple(key.split("."))
    if len(parts) < 2:
        raise AdminError(f"unknown config key: {key!r} (use section.key or an UPPER_SNAKE env var)")
    return parts


def get_value(doc: TOMLDocument, key: str) -> Any:
    node: Any = doc
    for part in resolve_key(key):
        if not isinstance(node, dict) or part not in node:
            raise AdminError(f"config key not set: {key}")
        node = node[part]
    return node


def _coerce(raw: str) -> Any:
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def set_value(doc: TOMLDocument, key: str, raw_value: str) -> tuple[str, ...]:
    parts = resolve_key(key)
    node: Any = doc
    for part in parts[:-1]:
        if part not in node:
            node[part] = tomlkit.table()
        node = node[part]
    leaf = parts[-1]
    # env-section values stay strings verbatim; typed sections get coerced
    is_env_value = len(parts) == 3 and parts[1] == "env"
    node[leaf] = raw_value if is_env_value else _coerce(raw_value)
    return parts


def unset_value(doc: TOMLDocument, key: str) -> None:
    parts = resolve_key(key)
    node: Any = doc
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise AdminError(f"config key not set: {key}")
        node = node[part]
    if parts[-1] not in node:
        raise AdminError(f"config key not set: {key}")
    del node[parts[-1]]


def redact(key: str, value: Any) -> Any:
    if isinstance(value, str) and value and SECRET_KEY_RE.search(key):
        return "********"
    return value


def flatten(doc: TOMLDocument) -> dict[str, Any]:
    flat: dict[str, Any] = {}

    def walk(node: dict[str, Any], prefix: str) -> None:
        for key, value in node.items():
            dotted = f"{prefix}{key}"
            if isinstance(value, dict):
                walk(value, f"{dotted}.")
            else:
                flat[dotted] = value

    walk(doc, "")
    return flat


# --- typed accessors -------------------------------------------------------


def port(doc: TOMLDocument, name: str) -> int:
    return int(doc.get("ports", {}).get(name, DEFAULT_PORTS[name]))


def provider(doc: TOMLDocument) -> str:
    value = str(doc.get("runtime", {}).get("provider", "podman"))
    if value not in {"docker", "podman"}:
        raise AdminError(f"invalid runtime.provider {value!r}; expected docker or podman")
    return value


def feature(doc: TOMLDocument, name: str) -> bool:
    return bool(doc.get("features", {}).get(name, False))


def channel(doc: TOMLDocument) -> str:
    return str(doc.get("install", {}).get("channel", "stable"))


def env_overrides(doc: TOMLDocument, section: str) -> dict[str, str]:
    values = doc.get(section, {}).get("env", {})
    return {str(k): str(v) for k, v in values.items()}


def installation_secret(doc: TOMLDocument) -> str:
    """The per-installation seed every derived local key hangs off.

    Never regenerate this for an existing config: it also derives the key that
    encrypts stored secrets, so a fresh one leaves every encrypted row in that
    installation unreadable.
    """

    internal = doc.setdefault("internal", tomlkit.table())
    key = internal.get("installation_secret")
    if not key:
        key = secrets.token_hex(16)
        internal["installation_secret"] = key
    return str(key)


def config_file_path(paths: LocalPaths) -> Path:
    return paths.config_file
