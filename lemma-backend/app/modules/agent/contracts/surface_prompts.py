"""Words the agent module writes for surface-side tools to hand the model.

A surface tool's *result* is read by the model exactly as a prompt is, so the
framing that tells it how to treat the data (here: other people's channel
messages are background, not instructions) is the agent's to write. The tool
supplies the data and the count; this supplies the sentence.

A submodule rather than `contracts/__init__` for the same reason the sibling
contract modules are: `__init__` is what anything wanting any contract at all
imports.
"""

from __future__ import annotations

from app.modules.agent.domain.surface_prompts import background_channel_context_note

__all__ = ["background_channel_context_note"]
