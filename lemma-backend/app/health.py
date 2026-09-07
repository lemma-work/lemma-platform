"""Liveness, readiness and capability probes.

Four endpoints that were nested inside `create_app`, which was 382 lines of a
999-line `app.py`. They close over nothing but the router they register on, so
they move as a router rather than as a factory.

`include_in_schema=False` on every route, as before: these are for probes and
for a person with `curl`, not for the published API.
"""

from opentelemetry import metrics
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.log.log import get_logger
from app.core.infrastructure.db.migration_state import schema_migration_state
from app.core.observability.dependency_incident import DependencyIncident
from app.core.observability import readiness
from app.core.observability.worker_liveness import worker_readiness_state
from app.core.security import supertokens_core_reachable
from app.sandbox_health import sandbox_capability
from app.core.infrastructure.channels.channel_service import channel_service

from app.core.infrastructure.db.session import database_reachable
from app.core.request_context import (
    create_inherited_task,
)

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
http_request_count = meter.create_counter("lemma.http.server.requests")
http_request_duration = meter.create_histogram("lemma.http.server.duration_ms")

OPENAPI_SCHEMA_RENAMES = {
    "fastapi___compat__v2__Body_file__upload": "DatastoreFileUploadRequest",
    "fastapi___compat__v2__Body_icon__upload": "IconUploadRequest",
    "fastapi___compat__v2__Body_app__bundle__upload": "AppBundleUploadRequest",
}


logger = get_logger(__name__)

router = APIRouter()


# Liveness: process/event-loop check only. No DB or network dependency, so
# it normally completes within ~100 ms. 503 when the event loop is wedged
# (lag over the unhealthy threshold), so a liveness probe restarts the
# process. A fully blocked loop can't serve this at all, which trips the
# probe's timeout — either way a hung process is restarted instead of
# hanging silently.
@router.get("/health/live", include_in_schema=False)
@router.get("/livez", include_in_schema=False)
async def health_live():
    from app.core.observability.loop_watchdog import (
        get_loop_lag_seconds,
        is_loop_healthy,
    )

    healthy = is_loop_healthy()
    payload = {
        "status": "ok" if healthy else "unhealthy",
        "loop_lag_seconds": round(get_loop_lag_seconds(), 3),
    }
    return JSONResponse(payload, status_code=200 if healthy else 503)


#: One degraded/recovered pair per dependency instead of a record per
#: probe. Threshold 1: readiness is asked constantly, so the first failure
#: is already the transition worth reporting.
_readiness_incidents = {
    name: DependencyIncident(name, logger=logger, degradation_threshold=1)
    for name in ("db", "redis", "supertokens")
}


# Readiness: bounded, concurrent checks for dependencies required to serve
# new work. Each check has ~1 s; the whole endpoint has a ~2 s deadline.
# 503 when not ready; only generic component states are exposed, never
# connection strings or provider responses.
@router.get("/health/ready", include_in_schema=False)
async def health_ready(request: Request):
    import asyncio as _asyncio

    async def _probe(name: str, check) -> bool:
        """One bounded dependency check that says why it failed, once.

        This used to be three silent `except Exception: return False`. The
        endpoint then reported `"db": "down"` with no reason anywhere --
        and a prober asks every few seconds, so the obvious repair, a
        record per attempt, is a wall of identical lines during exactly the
        outage someone is trying to read. `DependencyIncident` emits one
        degraded record when it starts failing and one when it recovers.
        """
        incident = _readiness_incidents[name]
        try:
            healthy = bool(await _asyncio.wait_for(check(), timeout=1.0))
        except Exception as exc:
            incident.record_failure(error_type=type(exc).__name__)
            return False
        if healthy:
            incident.record_success()
        else:
            incident.record_failure(error_type="unavailable")
        return healthy

    # Concurrent, each individually bounded and the set bounded by the
    # gather. SuperTokens is in here because `initialize_supertokens` makes
    # no network call while `verify_auth` calls the core on every
    # authenticated request: its outage leaves readiness at 200 and the
    # whole product unusable.
    tasks: list[_asyncio.Task[object]] = []
    try:
        embedded = getattr(request.app.state, "embedded_worker", False)
        tasks = [
            create_inherited_task(_probe("db", database_reachable)),
            create_inherited_task(_probe("redis", channel_service.ping)),
            create_inherited_task(_probe("supertokens", supertokens_core_reachable)),
            create_inherited_task(worker_readiness_state(embedded=embedded)),
            create_inherited_task(schema_migration_state()),
        ]
        db_ok, redis_ok, auth_ok, worker, schema = await _asyncio.wait_for(
            _asyncio.gather(*tasks), timeout=2.0
        )
    except Exception:
        # Readiness itself failing to run is not "the database is down";
        # it answers 503 either way, so the log is the only place the
        # difference can be seen.
        logger.error("app.health_ready.probe_failed.failed", exc_info=True)
        db_ok, redis_ok, auth_ok, worker, schema = False, False, False, None, None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

    report = readiness.build_readiness_report(
        components={
            "db": readiness.dependency_state(db_ok),
            "redis": readiness.dependency_state(redis_ok),
            "supertokens": readiness.dependency_state(auth_ok),
            "worker": readiness.worker_state(worker),
            "migrations": readiness.migrations_state(schema) if schema else None,
        },
        instance_id=settings.lemma_runtime_instance_id,
    )
    return JSONResponse(report.payload, status_code=report.status_code)


@router.get("/health/capabilities", include_in_schema=False)
async def health_capabilities():
    from app.modules.datastore.module import embedding_capability
    from app.modules.pod_bundle.config import pod_bundle_settings

    embeddings = embedding_capability()
    payload = {
        "status": (
            "degraded" if embeddings.status == "degraded" else embeddings.status
        ),
        "capabilities": {
            "embeddings": {
                "status": embeddings.status,
                "detail": embeddings.detail,
            }
        },
    }
    payload["capabilities"]["sandbox"] = sandbox_capability()
    if settings.lemma_local_ai_ready is not None:
        payload["capabilities"]["ai_profile"] = {
            "status": "ready" if settings.lemma_local_ai_ready else "needs_setup",
            "detail": (
                "Local AI provider is configured"
                if settings.lemma_local_ai_ready
                else "Configure an AI provider in Lemma Control Center"
            ),
        }
    # What this deployment is configured to *do*, for a client that has to
    # decide whether a behaviour it depends on is reachable here at all. The
    # product scenario suite reads this rather than a local `.env` file:
    # pointed at a deployment, a local file describes a different machine,
    # so a suite trusting it skips and runs for the wrong reasons.
    #
    # `environment` and `llm_mode` are reported everywhere. A deployment
    # serving the scripted test model is misconfigured, and that is worth
    # being visible rather than hidden.
    #
    # The rest is this deployment's security posture — whether signup is
    # rate limited, whether a connector may reach a private address — and
    # the honest answer to a stranger asking "are your gates on?" is that it
    # is none of their business. Written as "not production", that was one
    # environment value narrower than the principle: a staging or preview
    # deployment on the internet runs as `development` and advertised which
    # of its abuse controls were off. The block is for the scenario suite,
    # which runs locally.
    configuration: dict[str, object] = {
        "environment": settings.environment,
        "llm_mode": settings.e2e_llm_mode,
    }
    if settings.is_local_mode():
        configuration |= {
            "abuse_protection": settings.auth_abuse_protection_enabled,
            "altcha": settings.auth_altcha_enabled,
            "email_verification_required": (settings.auth_email_verification_required),
            "email_deliverability_checks": (
                settings.auth_email_deliverability_checks_enabled
            ),
            "disposable_email_domains": (
                settings.auth_disposable_email_domains_enabled
            ),
            "private_network_targets": (
                settings.connector_allow_private_network_targets
            ),
            "role_cache_ttl_seconds": (settings.authorization_role_cache_ttl_seconds),
            "bundle_daily_export_limit": (
                pod_bundle_settings.pod_bundle_daily_export_limit
            ),
            "bundle_daily_import_limit": (
                pod_bundle_settings.pod_bundle_daily_import_limit
            ),
            "usage_limit_overrides": bool(settings.usage_org_limit_overrides_json),
        }
    payload["configuration"] = configuration
    if settings.lemma_runtime_instance_id:
        payload["instance_id"] = settings.lemma_runtime_instance_id
    return payload


# Compatibility alias for /health/live during probe migration.
@router.get("/health", include_in_schema=False)
async def health_alias():
    from app.core.observability.loop_watchdog import (
        get_loop_lag_seconds,
        is_loop_healthy,
    )

    healthy = is_loop_healthy()
    payload = {
        "status": "ok" if healthy else "unhealthy",
        "loop_lag_seconds": round(get_loop_lag_seconds(), 3),
    }
    return JSONResponse(payload, status_code=200 if healthy else 503)
