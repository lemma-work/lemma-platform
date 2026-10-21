"""Validating and carrying a runtime profile's Agent Host selections.

Split out of ``agent_host`` because it answers a different question. That module
is the wire contract — what a host and Lemma say to each other. This is policy
over one harness's published options: whether a selection is allowed at all, and
which of a profile's selections survive the harness republishing its options
with different values.
"""

from __future__ import annotations

from app.modules.agent.domain.agent_host_options import agent_host_option_is_selectable
from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.value_objects import JsonObject


#: The key a model entry carries the options it offers under. Absent means the
#: model offers what the harness publishes at the top level.
LEMMA_OPTIONS_KEY = "lemma_options"


def _category(option: dict[str, object]) -> str:
    return str(option.get("category") or "").strip()


def _entry_value(item: dict[str, object]) -> object:
    return item["value"] if "value" in item else item.get("id")


def options_for_model(
    config_options: list[object], model_name: str | None
) -> list[JsonObject]:
    """The non-model options a harness offers when ``model_name`` is selected.

    A harness's options depend on its model: Claude Code offers a ``max``
    effort with one model and not another. The host publishes that per model,
    as ``lemma_options`` on the model's entry; a model without the key, a model
    the harness does not list, and no model at all (the harness's own default)
    all get the top-level options, which describe the default model.

    The model option itself is never in the result: a model is chosen through
    the model name, not as a selection.
    """
    top_level = [
        option
        for option in config_options
        if isinstance(option, dict) and _category(option) != "model"
    ]
    name = (model_name or "").strip()
    if not name:
        return top_level
    for option in config_options:
        if not isinstance(option, dict) or _category(option) != "model":
            continue
        entries = option.get("options")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or _entry_value(entry) != name:
                continue
            per_model = entry.get(LEMMA_OPTIONS_KEY)
            if not isinstance(per_model, list):
                return top_level
            return [
                nested
                for nested in per_model
                if isinstance(nested, dict) and _category(nested) != "model"
            ]
    return top_level


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


def _model_keys(config_options: list[object]) -> set[str]:
    """Both names a selection could give the model option by."""
    return {
        key
        for option in config_options
        if isinstance(option, dict) and _category(option) == "model"
        for key in (str(option.get("id") or "").strip(), _category(option))
        if key
    }


def validate_agent_host_selections(
    *,
    config_options: list[object],
    selections: JsonObject,
    model_name: str | None = None,
) -> JsonObject:
    """Validate provider-owned selections without translating their values.

    Against the options ``model_name`` offers (:func:`options_for_model`), so a
    value only one model takes is accepted when that model is the one chosen.
    ``None`` is the harness's own default model.
    """
    model_keys = _model_keys(config_options)
    options_by_key = _agent_host_options_by_key(
        list(options_for_model(config_options, model_name))
    )

    normalized: JsonObject = {}
    for key, value in selections.items():
        normalized_key = str(key).strip()
        if normalized_key in model_keys:
            raise RuntimeProfileValidationError(
                "Model must be configured through default_model_name"
            )
        option = options_by_key.get(normalized_key)
        if option is None:
            raise RuntimeProfileValidationError(
                f"Unknown Agent Host configuration selection: {key}"
            )
        # Membership in what the host published is the whole check. The host
        # owns the permission policy: it removes every value that would turn
        # off the approval gate (`bypassPermissions`, `acceptEdits`, ...)
        # before publishing, and refuses one again at session setup. This used
        # to be re-implemented here with a substring match, which read `model`
        # as a mode and missed three of the values the host refuses -- two
        # rule sets that disagreed, so a profile could save and then fail on
        # its first run. Plan mode, which the host publishes, is a choice a
        # person may make.
        if not _option_accepts(option, value):
            raise RuntimeProfileValidationError(
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
        raise RuntimeProfileValidationError("default_model_name cannot be empty")
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
        raise RuntimeProfileValidationError(
            "default_model_name is not offered by this harness"
        )
    return normalized


def carry_agent_host_selections(
    *,
    config_options: list[object],
    selections: JsonObject,
    model_name: str | None = None,
) -> JsonObject:
    """Carry saved selections across a harness re-publish, dropping what moved.

    The lenient sibling of :func:`validate_agent_host_selections`, for the one
    caller that is not a user pressing Save: a run already in flight whose
    harness re-published a different set of options underneath it. There, a
    selection the harness no longer offers is news about the harness, not a
    mistake by the person -- refusing it would fail a run over a value nobody
    chose to change.

    So an unknown key and a value that is no longer a member are *dropped*, and
    the harness applies its own default for them. Judged, like the strict
    sibling, against the options ``model_name`` offers.

    Nothing here refuses: the host published only the values it allows, and
    it refuses a disallowed one again at session setup.
    """
    model_keys = _model_keys(config_options)
    options_by_key = _agent_host_options_by_key(
        list(options_for_model(config_options, model_name))
    )
    carried: JsonObject = {}
    for key, value in selections.items():
        normalized_key = str(key).strip()
        if normalized_key in model_keys:
            continue
        option = options_by_key.get(normalized_key)
        if option is None:
            continue
        if not _option_accepts(option, value):
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
    except RuntimeProfileValidationError:
        return None


_SCALAR_TYPES: tuple[type, ...] = (bool, int, float, str)


def _option_accepts(option: dict[str, object], value: object) -> bool:
    """Whether ``value`` is one the harness published for ``option``.

    An enumerated option takes a member. One with no enumeration -- a free
    text field such as a working directory -- used to take anything at all, a
    nested object or a list forwarded to the host as a setting. It now takes a
    scalar, of the same type as the value the harness reports it holds when it
    reports one.
    """
    # A type Lemma cannot offer takes nothing: the host does not publish one,
    # and a stored option from before that check is not a choice either.
    if not agent_host_option_is_selectable(option):
        return False
    allowed_values = _agent_host_option_values(option.get("options"))
    if allowed_values:
        return value in allowed_values
    if not isinstance(value, _SCALAR_TYPES):
        return False
    current = option.get("current_value")
    if not isinstance(current, _SCALAR_TYPES):
        return True
    # bool is an int; a toggle must not take 3, nor a number True.
    return type(value) is type(current) or (
        not isinstance(value, bool)
        and not isinstance(current, bool)
        and isinstance(value, int | float)
        and isinstance(current, int | float)
    )


def _agent_host_option_values(raw_options: object) -> list[object]:
    """Every value an option offers, its groups' choices included.

    ACP lets an agent group a select's choices (``{group, name, options}``).
    Read as a choice, a group has no value -- so an option offering only
    groups read as offering nothing, and "nothing" means any scalar is taken.
    """
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
        elif isinstance(item.get("options"), list):
            values.extend(_agent_host_option_values(item["options"]))
    return values
