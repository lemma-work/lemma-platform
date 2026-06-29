from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

HARNESS_BINARIES = {
    "CODEX": "codex",
    "CLAUDE_CODE": "claude",
    "OPENCODE": "opencode",
    "CURSOR": "cursor-agent",
    "ANTIGRAVITY": "agy",
}

# Claude Code's bare ``sonnet``/``opus`` aliases resolve to the *latest* model,
# which currently defaults to the 1M-context beta variant. That variant requires
# usage-based billing credits, so a user on a plain Pro/Max plan who picks
# "sonnet" gets a hard "Usage credits required for 1M context" failure on their
# first message. We instead advertise the full, standard-context model ids so
# the default path never opts into the paid 1M window.
#
# ``provider_model_name`` is what we hand to ``claude --model``; ``name`` stays
# the friendly alias so it remains a stable selection key and existing saved
# profiles keep working. Update these ids when Claude Code ships a newer model
# (the alias->id mapping is the one spot that drifts with the CLI version).
CLAUDE_CODE_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "sonnet",
        "display_name": "Claude Sonnet 4.6",
        "provider_model_name": "claude-sonnet-4-6",
        "metadata": {"alias": "sonnet", "context_window": "standard"},
    },
    {
        "name": "opus",
        "display_name": "Claude Opus 4.8",
        "provider_model_name": "claude-opus-4-8",
        "metadata": {"alias": "opus", "context_window": "standard"},
    },
)

# Bare alias -> standard-context model id, used to rewrite ``--model`` for
# already-saved profiles (and any caller) that still send the raw alias.
CLAUDE_CODE_STANDARD_MODEL_BY_ALIAS: dict[str, str] = {
    str(entry["name"]): str(entry["provider_model_name"])
    for entry in CLAUDE_CODE_MODEL_CATALOG
}


def discover_harness_catalog() -> dict[str, dict[str, Any]]:
    return {
        harness_kind: discover_harness(harness_kind, binary)
        for harness_kind, binary in HARNESS_BINARIES.items()
    }


def discover_harness(harness_kind: str, binary: str) -> dict[str, Any]:
    path = shutil.which(binary)
    if path is None:
        return {"available": False, "binary": binary, "models": []}
    model_catalog, model_discovery_error = discover_harness_model_entries(harness_kind, binary)
    payload: dict[str, Any] = {
        "available": True,
        "binary": binary,
        "path": path,
        "version": binary_version(binary),
        # Flat list kept for backward compatibility with older readers.
        "models": [str(entry["name"]) for entry in model_catalog],
        # Structured entries carry display names, the provider model id we hand
        # to the harness, and metadata (context window, etc.) for the picker.
        "model_catalog": model_catalog,
        "display_name": harness_kind.replace("_", " ").title(),
    }
    if model_discovery_error:
        payload["model_discovery_error"] = model_discovery_error
    return payload


def discover_harness_model_entries(
    harness_kind: str, binary: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Structured model catalog for a harness.

    Each entry is ``{name, display_name, provider_model_name, metadata}``.
    ``name`` is the user-facing/selection key; ``provider_model_name`` is what
    gets passed to the harness CLI. For most harnesses these are identical, but
    Claude Code maps friendly aliases to full standard-context model ids.
    """
    configured = configured_harness_models(harness_kind)
    if configured is not None:
        return [_plain_model_entry(name) for name in configured], None
    try:
        if harness_kind == "CODEX":
            return [_plain_model_entry(name) for name in discover_codex_models(binary)], None
        if harness_kind == "OPENCODE":
            return [_plain_model_entry(name) for name in discover_opencode_models(binary)], None
        if harness_kind == "CLAUDE_CODE":
            return discover_claude_code_model_entries(binary), None
        if harness_kind == "CURSOR":
            return discover_cursor_model_entries(binary), None
        if harness_kind == "ANTIGRAVITY":
            return discover_antigravity_model_entries(binary), None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    return [], None


def discover_harness_models(harness_kind: str, binary: str) -> tuple[list[str], str | None]:
    entries, error = discover_harness_model_entries(harness_kind, binary)
    return [str(entry["name"]) for entry in entries], error


def _plain_model_entry(name: str) -> dict[str, Any]:
    """A catalog entry whose selection name and provider id are the same."""
    return {
        "name": name,
        "display_name": name,
        "provider_model_name": name,
        "metadata": {},
    }


def normalize_provider_model_name(harness_kind: str, model_name: str) -> str:
    """Rewrite a model name to the string we actually hand the harness CLI.

    For Claude Code this maps the bare ``sonnet``/``opus`` aliases to their
    full standard-context model ids, so callers that send the raw alias (e.g.
    profiles saved before this change) don't fall into the paid 1M-context
    variant. Unknown names (full ids, ``default``, other harnesses) pass
    through unchanged.
    """
    if harness_kind == "CLAUDE_CODE":
        return CLAUDE_CODE_STANDARD_MODEL_BY_ALIAS.get(model_name.strip(), model_name)
    return model_name


def configured_harness_models(harness_kind: str) -> list[str] | None:
    raw = os.getenv(f"LEMMA_DAEMON_{harness_kind}_MODELS")
    if raw is None:
        raw = os.getenv("LEMMA_DAEMON_MODELS")
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in raw.split(",")]
    if not isinstance(parsed, list):
        raise RuntimeError(f"Invalid model override for {harness_kind}: expected list")
    return _unique_model_names(str(item) for item in parsed)


def discover_codex_models(binary: str) -> list[str]:
    completed = run_catalog_command([binary, "debug", "models"])
    payload = load_json_from_output(completed.stdout or completed.stderr)
    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        return []
    return _unique_model_names(
        str(model.get("slug") or model.get("id") or model.get("name"))
        for model in raw_models
        if isinstance(model, dict)
    )


def discover_opencode_models(binary: str) -> list[str]:
    completed = run_catalog_command([binary, "models"])
    text = completed.stdout or completed.stderr
    payload = load_json_from_output(text)
    if payload is not None:
        models = _opencode_models_from_json(payload)
        if models:
            return _order_opencode_models(models)
    return _order_opencode_models(_opencode_models_from_text(text))


def _order_opencode_models(models: list[str]) -> list[str]:
    """Keep reliable provider models ahead of the rate-limited free tier.

    OpenCode's ``*-free`` (zen) models are slow and frequently rate-limited,
    which surfaces as a turn that "ends without assistant output". Default
    selection picks the first model, so push the free tier to the end to avoid
    landing a new runtime on a flaky default. Stable: order within each group is
    preserved.
    """
    return sorted(models, key=lambda model: 1 if "free" in model.lower() else 0)


def discover_claude_code_model_entries(binary: str) -> list[dict[str, Any]]:
    completed = run_catalog_command([binary, "--help"])
    text = f"{completed.stdout}\n{completed.stderr}"
    entries = [
        _copy_model_entry(entry)
        for entry in CLAUDE_CODE_MODEL_CATALOG
        if _claude_alias_advertised(str(entry["name"]), text)
    ]
    return entries or [_copy_model_entry(entry) for entry in CLAUDE_CODE_MODEL_CATALOG]


def _claude_alias_advertised(alias: str, help_text: str) -> bool:
    return (
        f"'{alias}'" in help_text
        or f'"{alias}"' in help_text
        or f" {alias}" in help_text
    )


def _copy_model_entry(entry: dict[str, Any]) -> dict[str, Any]:
    copied = dict(entry)
    copied["metadata"] = dict(entry.get("metadata") or {})
    return copied


def discover_cursor_model_entries(binary: str) -> list[dict[str, Any]]:
    """Parse ``cursor-agent models`` lines of the form ``<id> - <Label>``.

    ``id`` (e.g. ``gpt-5.3-codex-low``, ``auto``) is the ``--model`` value and the
    selection key; the label is the friendly display name. Header/blank lines and
    anything without an id-shaped first token are skipped.
    """
    completed = run_catalog_command([binary, "models"])
    text = completed.stdout or completed.stderr
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text.replace("\x1b", " ").splitlines():
        stripped = line.strip()
        if " - " not in stripped:
            continue
        model_id, _, label = stripped.partition(" - ")
        model_id = model_id.strip()
        label = label.strip()
        if not model_id or " " in model_id or model_id in seen:
            continue
        if label.endswith("(current)"):
            label = label[: -len("(current)")].strip()
        seen.add(model_id)
        entries.append(
            {
                "name": model_id,
                "display_name": label or model_id,
                "provider_model_name": model_id,
                "metadata": {},
            }
        )
    return entries


def discover_antigravity_model_entries(binary: str) -> list[dict[str, Any]]:
    """Parse ``agy models`` output.

    Antigravity lists plain display names (e.g. ``Gemini 3.5 Flash (Medium)``,
    ``Claude Sonnet 4.6 (Thinking)``) and accepts that same string as ``--model``,
    so name == display_name == provider_model_name. Banner/header lines are
    skipped.
    """
    completed = run_catalog_command([binary, "models"])
    text = completed.stdout or completed.stderr
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text.replace("\x1b", " ").splitlines():
        name = line.strip()
        if not name or name in seen:
            continue
        if name.lower().startswith(("available", "models", "usage", "error", "warning", "no ")):
            continue
        seen.add(name)
        entries.append(
            {
                "name": name,
                "display_name": name,
                "provider_model_name": name,
                "metadata": {},
            }
        )
    return entries


def run_catalog_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(message or f"{command[0]} exited with {completed.returncode}")
    return completed


def load_json_from_output(text: str) -> object | None:
    decoder = json.JSONDecoder()
    starts = [index for index, char in enumerate(text) if char in "[{"]
    for start in starts:
        try:
            payload, _ = decoder.raw_decode(text[start:])
            return payload
        except json.JSONDecodeError:
            continue
    return None


def binary_version(binary: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (completed.stdout or completed.stderr).strip()
    return output or None


def _opencode_models_from_json(payload: object) -> list[str]:
    models: list[str] = []

    def walk(value: object, provider: str | None = None) -> None:
        if isinstance(value, dict):
            next_provider = str(
                value.get("providerID")
                or value.get("provider_id")
                or value.get("provider")
                or provider
                or ""
            )
            model = value.get("modelID") or value.get("model_id") or value.get("id")
            if model:
                model_name = str(model)
                models.append(
                    f"{next_provider}/{model_name}"
                    if next_provider and "/" not in model_name
                    else model_name
                )
            for child in value.values():
                walk(child, next_provider or provider)
            return
        if isinstance(value, list):
            for child in value:
                walk(child, provider)

    walk(payload)
    return _unique_model_names(models)


def _opencode_models_from_text(text: str) -> list[str]:
    separators = " \t\n\r,;|"
    tokens = (
        token.strip(separators + "'\"`")
        for token in text.replace("\x1b", " ").split()
    )
    return _unique_model_names(
        token
        for token in tokens
        if "/" in token and not token.startswith(("http://", "https://"))
    )


def _unique_model_names(models: object) -> list[str]:
    names: list[str] = []
    for raw in models:
        model = str(raw).strip()
        if model and model not in names:
            names.append(model)
    return names
