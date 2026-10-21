"""Per-namespace limits: the port, the settings-backed default, and the seam.

Modelled on usage's ``ConfiguredUsageLimitPort``: a deployment states its limits
in settings, and a plan-backed provider (lemma-cloud) replaces the whole port at
startup through ``install_work_limit_port``. Nothing in the runtime knows about
plans.

The runtime reads limits in two places only -- when an enqueue creates a
namespace's queue row, and when a dispatcher refreshes caps for namespaces with
queued work -- never per claimed item. A provider is expected to cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.work.config import default_max_running, work_settings
from app.core.infrastructure.work.rates import Rate, default_rates, parse_rates
from app.core.infrastructure.work.types import WorkClass
from app.core.log.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "ConfiguredWorkLimitPort",
    "NamespaceDirectory",
    "Rate",
    "WorkLimitPort",
    "WorkLimits",
    "current_namespace_directory",
    "current_work_limit_port",
    "declare_namespace_directory",
    "install_work_limit_port",
    "reset_work_limit_port",
]


@dataclass(frozen=True, slots=True)
class WorkLimits:
    max_running: dict[WorkClass, int]
    #: Every named rate for the namespace: deployment defaults with any
    #: override on top. Stored on the namespace's queue rows and enforced at
    #: claim for the kinds that declare them.
    rates: dict[str, Rate]

    def cap(self, cls: WorkClass) -> int:
        return self.max_running.get(cls, default_max_running(cls))


class WorkLimitPort(Protocol):
    async def limits_for(self, ns: str) -> WorkLimits: ...


class NamespaceDirectory(Protocol):
    """Who owns a namespace, for overrides keyed by organization.

    Core cannot read the pods or organizations tables, so the pod module
    supplies this through ``LemmaModule.namespace_directory``; without one, org
    rules match only ``org:`` namespaces and slug rules match nothing.
    """

    async def organization_of(self, ns: str) -> tuple[UUID, str] | None: ...


@dataclass(frozen=True, slots=True)
class OverrideRule:
    #: (tier, prefix length): ns/pod_id beats org_id beats slug beats any
    #: prefix, and a longer prefix beats a shorter one.
    specificity: tuple[int, int]
    key: str
    value: str
    max_running: tuple[tuple[WorkClass, int], ...]
    rates: tuple[tuple[str, Rate], ...]


_TIERS = {"ns": 4, "pod_id": 4, "org_id": 3, "slug": 2, "slug_prefix": 1}


def _class_caps(raw: JsonValue) -> tuple[tuple[WorkClass, int], ...]:
    if not isinstance(raw, dict):
        return ()
    valid = {c.value for c in WorkClass}
    return tuple(
        (WorkClass(name), value)
        for name, value in raw.items()
        if name in valid and isinstance(value, int) and value >= 0
    )


def _rule(entry: JsonValue) -> OverrideRule | None:
    if not isinstance(entry, dict):
        return None
    for key, tier in _TIERS.items():
        value = entry.get(key)
        if isinstance(value, str) and value:
            caps = _class_caps(entry.get("max_running"))
            rates = parse_rates(entry.get("rates"))
            if not caps and not rates:
                return None
            prefix_length = len(value) if key == "slug_prefix" else 0
            return OverrideRule((tier, prefix_length), key, value, caps, rates)
    return None


@lru_cache(maxsize=8)
def parse_overrides(raw: str) -> tuple[OverrideRule, ...]:
    """Override rules from ``WORK_LIMIT_OVERRIDES_JSON``.

    Malformed input yields no rules and warns once per distinct value: a cap
    that silently does not apply is worse than one that refuses, but taking the
    whole runtime down over a typo is worse still.
    """
    if not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("work.limit_overrides.unparseable")
        return ()
    if not isinstance(parsed, list):
        logger.warning("work.limit_overrides.not_a_list")
        return ()
    rules: list[OverrideRule] = []
    for position, entry in enumerate(parsed):
        rule = _rule(entry)
        if rule is None:
            logger.warning("work.limit_overrides.entry_ignored", position=position)
            continue
        rules.append(rule)
    return tuple(rules)


def _matches(rule: OverrideRule, ns: str, owner: tuple[UUID, str] | None) -> bool:
    key, value = rule.key, rule.value
    if key == "ns":
        return ns == value
    if key == "pod_id":
        return ns == f"pod:{value}"
    if key == "org_id":
        return ns == f"org:{value}" or (owner is not None and str(owner[0]) == value)
    if owner is None:
        return False
    if key == "slug":
        return owner[1] == value
    return owner[1].startswith(value)


def select_override(
    rules: tuple[OverrideRule, ...], ns: str, owner: tuple[UUID, str] | None
) -> OverrideRule | None:
    """The most specific matching rule; a tie goes to the one written last."""
    best: tuple[tuple[int, int], int] | None = None
    chosen: OverrideRule | None = None
    for position, rule in enumerate(rules):
        if not _matches(rule, ns, owner):
            continue
        rank = (rule.specificity, position)
        if best is None or rank > best:
            best, chosen = rank, rule
    return chosen


class ConfiguredWorkLimitPort:
    """Settings defaults plus ``WORK_LIMIT_OVERRIDES_JSON``."""

    def __init__(
        self,
        *,
        raw_overrides: str | None = None,
        directory: NamespaceDirectory | None = None,
    ) -> None:
        self._raw = raw_overrides
        self._directory = directory

    async def limits_for(self, ns: str) -> WorkLimits:
        raw = (
            work_settings.work_limit_overrides_json if self._raw is None else self._raw
        )
        rules = parse_overrides(raw)
        caps = {cls: default_max_running(cls) for cls in WorkClass}
        rates = default_rates()
        if not rules:
            return WorkLimits(max_running=caps, rates=rates)
        owner = None
        directory = self._directory or current_namespace_directory()
        if directory is not None and ns.startswith("pod:"):
            owner = await directory.organization_of(ns)
        rule = select_override(rules, ns, owner)
        if rule is None:
            return WorkLimits(max_running=caps, rates=rates)
        caps.update(dict(rule.max_running))
        rates.update(dict(rule.rates))
        return WorkLimits(max_running=caps, rates=rates)


@dataclass(slots=True)
class _Installed:
    port: WorkLimitPort | None = None
    directory: NamespaceDirectory | None = None


_installed = _Installed()


def install_work_limit_port(port: WorkLimitPort) -> None:
    """Replace the settings-backed port for this process (lemma-cloud's plan provider)."""
    _installed.port = port


def reset_work_limit_port() -> None:
    _installed.port = None


def current_work_limit_port() -> WorkLimitPort:
    return _installed.port or ConfiguredWorkLimitPort()


def declare_namespace_directory(directory: NamespaceDirectory | None) -> None:
    """Register who can say which organization owns a pod (``configure_namespace_directory``)."""
    _installed.directory = directory


def current_namespace_directory() -> NamespaceDirectory | None:
    return _installed.directory
