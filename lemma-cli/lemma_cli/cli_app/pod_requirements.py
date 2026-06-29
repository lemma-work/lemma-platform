"""Bundle requirements/capabilities extraction.

The implementation now lives in the shared ``lemma_pod_bundle`` package so the
CLI and the backend derive identical facts from one source of truth. This module
re-exports it so existing CLI imports keep working unchanged.
"""

from __future__ import annotations

from lemma_pod_bundle.requirements import (
    CAPABILITY_TIER_ORDER,
    extract_requirements,
    read_requirements,
    unresolved_requirements,
)

__all__ = [
    "CAPABILITY_TIER_ORDER",
    "extract_requirements",
    "read_requirements",
    "unresolved_requirements",
]
