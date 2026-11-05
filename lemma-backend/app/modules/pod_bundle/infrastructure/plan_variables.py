"""The import variables a bundle's ``pod.json`` asks the importer for."""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.pod_bundle.domain.state import VariableSpec

_DEPRECATED_ACCOUNT_VARIABLE = (
    "Deprecated: one of your connected accounts. Leave it empty and choose the "
    "pod connector after import, or give an account id to use the connector "
    "serving it."
)


def variables_from_manifest(pod_manifest: Mapping[str, object]) -> list[VariableSpec]:
    """Turn ``pod.json -> variables`` into typed specs.

    A ``pod_member`` variable auto-resolves to the importing user, and a
    ``free`` variable (e.g. an app slug) is required only when it has no
    default.

    An ``account`` variable is deprecated (resources name a pod connector now)
    but still imports, as an optional account id the schedule step turns into
    the pod connector serving it -- so a template nobody re-exported works."""
    raw = pod_manifest.get("variables")
    if not isinstance(raw, dict):
        return []
    specs: list[VariableSpec] = []
    for name, raw_meta in raw.items():
        # A hand-written manifest may say anything; only a mapping says something.
        meta: Mapping[str, object] = raw_meta if isinstance(raw_meta, dict) else {}
        vtype = str(meta.get("type") or "").lower()
        kind = "pod_member" if vtype in ("member", "pod_member") else "free"
        default = meta.get("default")
        raw_description = meta.get("description")
        description = raw_description if isinstance(raw_description, str) else None
        if vtype == "account":
            default, description = None, _DEPRECATED_ACCOUNT_VARIABLE
        specs.append(
            VariableSpec(
                name=str(name),
                kind=kind,  # type: ignore[arg-type]
                description=description,
                required=kind == "free" and default is None and vtype != "account",
                default=str(default) if default is not None else None,
            )
        )
    return specs


__all__ = ["variables_from_manifest"]
