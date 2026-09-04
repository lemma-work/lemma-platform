"""Validating and carrying a runtime profile's Agent Host selections.

Split out of ``agent_host`` because it answers a different question. That module
is the wire contract — what a host and Lemma say to each other. This is policy
over one harness's published options: whether a selection is allowed at all, and
which of a profile's selections survive the harness republishing its options
with different values.
"""

from __future__ import annotations

from app.modules.agent.domain.value_objects import JsonObject


def _agent_host_options_by_key(
    config_options: list[object],
) -> dict[str, dict[str, object]]:
    """Index a harness's options by both the names a selection may use."""
    options_by_key: dict[str, dict[str, object]] = {}
    for raw_option in config_options:
        if not isinstance(raw_option, dict):
            continue
        option_id = str(raw_option.get("id") or "").strip()
        category = str(raw_option.get("category") or "").strip()
        if option_id:
            options_by_key[option_id] = raw_option
        if category:
            options_by_key[category] = raw_option
    return options_by_key


def validate_agent_host_selections(
    *,
    config_options: list[object],
    selections: JsonObject,
) -> JsonObject:
    """Validate provider-owned selections without translating their values."""
    options_by_key = _agent_host_options_by_key(config_options)

    normalized: JsonObject = {}
    for key, value in selections.items():
        normalized_key = str(key).strip()
        option = options_by_key.get(normalized_key)
        if option is None:
            raise ValueError(f"Unknown Agent Host configuration selection: {key}")
        option_category = str(option.get("category") or "").strip()
        if option_category == "model":
            raise ValueError("Model must be configured through default_model_name")
        if option_category in _PLATFORM_OWNED_OPTION_CATEGORIES:
            # Approval/sandbox presets and turn-to-turn collaboration are
            # Lemma's, not a per-profile choice: approvals must be answered the
            # same way whichever harness runs, and a conversation already maps
            # to one session. Dropped rather than rejected so a profile saved
            # before this rule stays editable; the harness keeps applying its
            # own safe default, which is what the built-in harness does too.
            continue
        # Deny-list first, then membership - the order ``selection_is_allowed``
        # uses in acp.rs. It matters: harnesses *do* enumerate their
        # permission modes, so a value like ``bypassPermissions`` is a legal
        # member of the option's own list and the host refuses it anyway.
        # Checking membership first would let exactly the common case through,
        # to save cleanly and then fail at session setup on the first run.
        if _is_disallowed_policy_selection(option, value):
            raise ValueError(
                f"That value is not allowed for Agent Host configuration: {key}"
            )
        allowed_values = _agent_host_option_values(option.get("options"))
        if allowed_values and value not in allowed_values:
            raise ValueError(
                f"Invalid value for Agent Host configuration selection: {key}"
            )
        normalized[normalized_key] = value
    return normalized


def validate_agent_host_model(
    *,
    config_options: list[object],
    model_name: str | None,
) -> str | None:
    if model_name is None:
        return None
    normalized = model_name.strip()
    if not normalized:
        raise ValueError("default_model_name cannot be empty")
    model_options: list[object] = []
    has_model_option = False
    for raw_option in config_options:
        if not isinstance(raw_option, dict):
            continue
        if str(raw_option.get("category") or "").strip() != "model":
            continue
        has_model_option = True
        model_options.extend(_agent_host_option_values(raw_option.get("options")))
    if not has_model_option or normalized not in model_options:
        raise ValueError("default_model_name is not offered by this harness")
    return normalized


class AgentHostSelectionRefused(ValueError):
    """A carried-over selection the re-published harness must not be given."""


def carry_agent_host_selections(
    *,
    config_options: list[object],
    selections: JsonObject,
) -> JsonObject:
    """Carry saved selections across a harness re-publish, dropping what moved.

    The lenient sibling of :func:`validate_agent_host_selections`, for the one
    caller that is not a user pressing Save: a run already in flight whose
    harness re-published a different set of options underneath it. There, a
    selection the harness no longer offers is news about the harness, not a
    mistake by the person -- refusing it would fail a run over a value nobody
    chose to change.

    So an unknown key and a value that is no longer a member are *dropped*, and
    the harness applies its own default for them.

    A policy-bearing value is the exception and still refuses, by raising:
    ``_is_disallowed_policy_selection`` is what stops a stored profile turning
    off the human-approval gate, and "the harness changed" is not a reason to
    stop enforcing it. The caller fails the run rather than dispatching it.
    """
    options_by_key = _agent_host_options_by_key(config_options)
    carried: JsonObject = {}
    for key, value in selections.items():
        normalized_key = str(key).strip()
        option = options_by_key.get(normalized_key)
        if option is None:
            continue
        option_category = str(option.get("category") or "").strip()
        if option_category == "model" or option_category in (
            _PLATFORM_OWNED_OPTION_CATEGORIES
        ):
            continue
        if _is_disallowed_policy_selection(option, value):
            raise AgentHostSelectionRefused(
                f"That value is not allowed for Agent Host configuration: {key}"
            )
        allowed_values = _agent_host_option_values(option.get("options"))
        if allowed_values and value not in allowed_values:
            continue
        carried[normalized_key] = value
    return carried


def carry_agent_host_model(
    *,
    config_options: list[object],
    model_name: str | None,
) -> str | None:
    """The pinned model if the re-published harness still offers it, else None.

    ``None`` means "let the harness use its own default", which is what an
    unpinned profile already sends. Unlike :func:`validate_agent_host_model`
    this never raises: a model is a preference, not a permission, and the
    codebase already treats it that way when a profile is edited
    (``runtime_profile_editor`` clears a stale pin rather than refusing) and
    when one is dispatched (``_selected_model`` falls back to the catalog).
    """
    if model_name is None:
        return None
    try:
        return validate_agent_host_model(
            config_options=config_options, model_name=model_name
        )
    except ValueError:
        return None


# Mirrors the Agent Host's own policy filter (desktop/agent-host/src/acp.rs:569-600):
# an option whose id or category mentions one of these governs what the coding
# agent is allowed to do without asking.
# Settings the platform owns, so a profile may not carry them. `mode` is the
# approval and sandboxing preset, and approvals are Lemma's job - a run asks, a
# human answers, identically whichever harness executes. `collaboration_mode`
# decides how state carries across turns, which the conversation already fixes
# by mapping to one session.
_PLATFORM_OWNED_OPTION_CATEGORIES = frozenset({"mode", "collaboration_mode"})

_POLICY_OPTION_MARKERS = ("mode", "permission", "approval", "sandbox")
_DISALLOWED_POLICY_VALUES = frozenset(
    {
        "bypasspermissions",
        "agentfullaccess",
        "fullaccess",
        "acceptedits",
        "yolo",
        "auto",
    }
)


def _is_disallowed_policy_selection(option: dict[str, object], value: object) -> bool:
    if not isinstance(value, str):
        return False
    identity = f"{option.get('id') or ''} {option.get('category') or ''}".lower()
    if not any(marker in identity for marker in _POLICY_OPTION_MARKERS):
        return False
    normalized = "".join(ch for ch in value if ch.isalnum()).lower()
    return normalized in _DISALLOWED_POLICY_VALUES


def _agent_host_option_values(raw_options: object) -> list[object]:
    if not isinstance(raw_options, list):
        return []
    values: list[object] = []
    for item in raw_options:
        if not isinstance(item, dict):
            values.append(item)
            continue
        if "value" in item:
            values.append(item["value"])
        elif "id" in item:
            values.append(item["id"])
    return values
