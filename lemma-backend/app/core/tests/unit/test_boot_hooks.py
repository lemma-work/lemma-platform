"""Every module boot hook is named here, with why it is allowed on the boot path.

A lifespan hook runs before a process serves anything, on every replica, on
every restart. One that walks the data -- a backfill over every pod schema, a
repair over every row -- is cheap on the day it is written and grows with every
pod created afterwards, until it is the slowest thing the process does and
nothing says so. That is how a grant backfill came to own most of API startup.

Adding a hook means adding a line below, and the line has to say why the hook
does not scale with data. A backfill does not belong here at all: it belongs in
a migration, or in a worker job. See the boot-hook note in AGENTS.md.
"""

from __future__ import annotations

from app.core.registry.contract import LemmaModule
from app.core.registry.installed import OSS_MODULES

#: ``<process>:<module>.<hook>`` -> why it may run at boot.
ALLOWED_BOOT_HOOKS: dict[str, str] = {
    "api:vault.api_vault_lifespan": (
        "unwraps the vault's key-encryption keys: a handful of rows, one per "
        "rotation, however many secrets there are"
    ),
    "worker:vault.worker_vault_lifespan": (
        "unwraps the vault's key-encryption keys: a handful of rows, one per "
        "rotation, however many secrets there are"
    ),
    "api:identity._close_user_cache": "shutdown only; closes Redis clients",
    "api:datastore._preload_local_embeddings": (
        "loads one embedding model (or starts it in the background); "
        "independent of stored data"
    ),
    "api:agent._report_system_model_pricing": (
        "compares the static model catalog with the static price table"
    ),
    "api:agent._drain_agent_host_links": "shutdown only; drains live links",
    "api:function._close_runtime_http_clients": "shutdown only; closes HTTP clients",
    "api:agent_surfaces._dedup_store_lifespan": (
        "logs webhook security settings; closes the dedupe store on shutdown"
    ),
    "api:agent_surfaces._telegram_manager_webhook_lifespan": (
        "starts one background registration task; does not await it"
    ),
    "api:workspace._close_workspace_clients": "shutdown only; closes sandbox clients",
    "worker:identity._configure_supertokens": "in-process SDK configuration",
    "worker:datastore._close_datastore_engine": "shutdown only; disposes the engine",
    "worker:datastore._preload_local_embeddings": (
        "loads one embedding model (or starts it in the background); "
        "independent of stored data"
    ),
    "worker:datastore._datastore_outbox_dispatcher": (
        "CREATE TABLE IF NOT EXISTS for one table, then a background dispatcher"
    ),
    "worker:datastore._close_reindex_queue": "shutdown only; closes the queue",
    "worker:schedule._reconcile_failure_breakers": (
        "reads only schedules already past the failure threshold, which the "
        "breaker keeps near empty"
    ),
    "worker:schedule._schedule_poller": "starts one background poller task",
    "worker:agent._report_system_model_pricing": (
        "compares the static model catalog with the static price table"
    ),
    "worker:function._close_runtime_http_clients": (
        "shutdown only; closes HTTP clients"
    ),
    "worker:agent_surfaces._surface_event_receiver": (
        "starts background receiver tasks; does not await them"
    ),
}


def _hook_name(hook: object) -> str:
    return getattr(hook, "__name__", type(hook).__name__)


def registered_boot_hooks(modules: tuple[LemmaModule, ...]) -> set[str]:
    hooks: set[str] = set()
    for module in modules:
        hooks.update(f"api:{module.name}.{_hook_name(h)}" for h in module.api_lifespans)
        hooks.update(
            f"worker:{module.name}.{_hook_name(h)}" for h in module.worker_lifespans
        )
    return hooks


def test_every_boot_hook_is_allowlisted_with_a_reason() -> None:
    unlisted = registered_boot_hooks(OSS_MODULES) - ALLOWED_BOOT_HOOKS.keys()
    assert not unlisted, (
        f"boot hooks without an entry in ALLOWED_BOOT_HOOKS: {sorted(unlisted)}. "
        "A boot hook must not scale with data -- backfills go in a migration or "
        "a worker job. If it genuinely does not, add it with the reason."
    )


def test_the_allowlist_names_only_hooks_that_exist() -> None:
    """A stale entry is a free pass waiting for the next hook of that name."""
    stale = ALLOWED_BOOT_HOOKS.keys() - registered_boot_hooks(OSS_MODULES)
    assert not stale, f"remove these from ALLOWED_BOOT_HOOKS: {sorted(stale)}"


def test_every_reason_says_something() -> None:
    assert all(reason.strip() for reason in ALLOWED_BOOT_HOOKS.values())
