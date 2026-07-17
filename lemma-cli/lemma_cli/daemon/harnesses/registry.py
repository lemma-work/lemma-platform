from __future__ import annotations

from .antigravity import AntigravityHarness
from .claude_code import ClaudeCodeHarness
from .codex import CodexHarness
from .cursor import CursorHarness
from .gg_coder import GgCoderHarness
from .opencode import OpenCodeHarness

_REGISTRY = {
    "CLAUDE_CODE": ClaudeCodeHarness(),
    "CODEX": CodexHarness(),
    "OPENCODE": OpenCodeHarness(),
    "CURSOR": CursorHarness(),
    "ANTIGRAVITY": AntigravityHarness(),
    "GG_CODER": GgCoderHarness(),
}


def get_harness(kind: str):
    harness = _REGISTRY.get(kind)
    if harness is None:
        raise RuntimeError(f"Unsupported daemon harness kind: {kind!r}")
    return harness
