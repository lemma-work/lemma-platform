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
    models, model_discovery_error = discover_harness_models(harness_kind, binary)
    payload: dict[str, Any] = {
        "available": True,
        "binary": binary,
        "path": path,
        "version": binary_version(binary),
        "models": models,
        "display_name": harness_kind.replace("_", " ").title(),
    }
    if model_discovery_error:
        payload["model_discovery_error"] = model_discovery_error
    return payload


def discover_harness_models(harness_kind: str, binary: str) -> tuple[list[str], str | None]:
    configured = configured_harness_models(harness_kind)
    if configured is not None:
        return configured, None
    try:
        if harness_kind == "CODEX":
            return discover_codex_models(binary), None
        if harness_kind == "OPENCODE":
            return discover_opencode_models(binary), None
        if harness_kind == "CLAUDE_CODE":
            return discover_claude_code_models(binary), None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    return [], None


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


def discover_claude_code_models(binary: str) -> list[str]:
    completed = run_catalog_command([binary, "--help"])
    text = f"{completed.stdout}\n{completed.stderr}"
    aliases = [
        model
        for model in ("sonnet", "opus")
        if f"'{model}'" in text or f'"{model}"' in text or f" {model}" in text
    ]
    return aliases or ["sonnet", "opus"]


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
