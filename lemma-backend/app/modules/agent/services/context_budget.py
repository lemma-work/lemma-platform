"""How much context a run may actually use, per model.

Compaction used to be governed by two global constants -- a 70k trigger and a
110k ceiling -- that no model had any say in. Both were tuned against a token
counter that over-counted images by two orders of magnitude, so they were
lowered on evidence that was wrong; and neither ever knew whether the model on
the other end had a 128k window or a million-token one. A run on a large model
was compacted at 70k for no reason, and a run on a small one had a "ceiling"
well above what the provider would accept.

A budget is a property of the model, so declare it on the model's catalog entry.
Precedence is most-specific-first: the model's own `context_window`, then a
deployment-wide default an operator can move with `AGENT_DEFAULT_CONTEXT_WINDOW_TOKENS`,
then the built-in default.

The two thresholds are fractions of whatever that resolves to, and the headroom
is deliberate. The prompt is not the whole request: tool schemas, the
instruction block, and the model's own reply all draw on the same window, and
our count is a close estimate rather than any provider's exact vocabulary.
Compacting at 80% and refusing to exceed 92% keeps the estimate error and the
response budget inside the window.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings

logger = get_logger(__name__)

#: Key under a catalog entry's ``metadata`` holding that model's window.
CONTEXT_WINDOW_METADATA_KEY = "context_window"

#: Below this a window is not a small model, it is a bad value.
MIN_CONTEXT_WINDOW_TOKENS = 4_000

#: Used when neither the model nor the deployment says anything usable.
#:
#: Deliberately conservative. A model that genuinely has a larger window declares
#: it on its catalog entry and gets the larger budget; this is what is safe when
#: nothing is known. Assuming more than a model has is how compaction fails to
#: fire until after the provider has already rejected the request.
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000

#: Compact here. Leaves room for tool schemas, instructions and the reply.
SUMMARIZATION_FRACTION = 0.80

#: Never exceed this. The last line before a provider rejection.
HARD_CEILING_FRACTION = 0.92


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """What one run may spend, derived from one model's window."""

    window: int
    summarization_token_limit: int
    hard_token_ceiling: int


def _coerce_window(value: object) -> int | None:
    """A usable window from catalog metadata, or None.

    Metadata is operator-authored JSON, so the value arrives as whatever was
    typed -- an int, a string, or a mistake.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str):
        try:
            candidate = int(value.strip())
        except ValueError:
            return None
    else:
        return None
    return candidate if candidate >= MIN_CONTEXT_WINDOW_TOKENS else None


def configured_default_context_window() -> int:
    """The deployment default, env taking precedence over settings.

    Matches `summarization_model`'s idiom: live env wins over the cached
    Settings singleton, so an operator can change it without a restart.
    """
    raw = os.getenv("AGENT_DEFAULT_CONTEXT_WINDOW_TOKENS")
    if raw is not None:
        coerced = _coerce_window(raw)
        if coerced is not None:
            return coerced
        logger.warning(
            "agent.context_budget.invalid_env_window.degraded",
            configured_value=raw,
        )
    # Coerced rather than trusted: this is the same operator-supplied number by
    # another route, and a settings file is no more careful than an env var.
    configured = _coerce_window(agent_settings.agent_default_context_window_tokens)
    return configured if configured is not None else DEFAULT_CONTEXT_WINDOW_TOKENS


def resolve_context_window(model: object | None) -> int:
    """This model's window: its own declaration, else the deployment default."""
    metadata = getattr(model, "metadata", None)
    if isinstance(metadata, dict) and CONTEXT_WINDOW_METADATA_KEY in metadata:
        declared = metadata[CONTEXT_WINDOW_METADATA_KEY]
        coerced = _coerce_window(declared)
        if coerced is not None:
            return coerced
        # Warning, not debug: a typo'd window silently reverting to the default
        # is how a small model ends up with a budget it cannot honour, and the
        # operator sees a setting that did nothing.
        logger.warning(
            "agent.context_budget.invalid_model_window.degraded",
            model_name=getattr(model, "name", None),
            configured_value=str(declared),
        )
    return configured_default_context_window()


def context_budget_for(model: object | None) -> ContextBudget:
    """The compaction trigger and hard ceiling for a run on this model."""
    window = resolve_context_window(model)
    return ContextBudget(
        window=window,
        summarization_token_limit=int(window * SUMMARIZATION_FRACTION),
        hard_token_ceiling=int(window * HARD_CEILING_FRACTION),
    )
