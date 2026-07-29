"""Pydantic AI capabilities for the in-process LEMMA harness.

Capabilities bundle behaviour (instructions, model settings, lifecycle hooks,
deferred tool discovery) that the in-process pydantic-ai agent gets on top of its
visible core toolsets. Remote harnesses (Codex/Claude-Code) do not use these —
they reach tools through the per-conversation MCP server instead.
"""

from __future__ import annotations

from app.modules.agent.capabilities.assembler import build_lemma_harness_tooling

__all__ = ["build_lemma_harness_tooling"]
