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
        # Membership in what the host published is the whole check. The host
        # owns the permission policy: it removes every value that would turn
        # off the approval gate (`bypassPermissions`, `acceptEdits`, ...)
        # before publishing, and refuses one again at session setup. This used
        # to be re-implemented here with a substring match, which read `model`
        # as a mode and missed three of the values the host refuses -- two
        # rule sets that disagreed, so a profile could save and then fail on
        # its first run. Plan mode, which the host publishes, is a choice a
        # person may make.
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

    Nothing here refuses: the host published only the values it allows, and
    it refuses a disallowed one again at session setup.
    """
    options_by_key = _agent_host_options_by_key(config_options)
    carried: JsonObject = {}
    for key, value in selections.items():
        normalized_key = str(key).strip()
        option = options_by_key.get(normalized_key)
        if option is None:
            continue
        option_category = str(option.get("category") or "").strip()
        if option_category == "model":
            continue
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
