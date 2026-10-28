"""What opens every message Lemma writes into a history for the model to read.

Two jobs: the model reads it as scaffolding rather than as something a person
said, and the compactor can tell these apart from real user turns so it does
not pin them forever. Every such notice opens with it, whichever part of the
history writes it (``services/runtime_history``, ``folded_turns``).
"""

from __future__ import annotations

SYNTHETIC_NOTICE_PREFIX = "[conversation history]"
