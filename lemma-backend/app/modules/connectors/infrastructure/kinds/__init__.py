"""Per-kind plugins: install validation, discovery, auth and execution."""

from app.modules.connectors.infrastructure.kinds.registry import (
    KindRegistry,
    build_kind_registry,
)

__all__ = ["KindRegistry", "build_kind_registry"]
