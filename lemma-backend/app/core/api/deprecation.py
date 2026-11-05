"""Deprecated routes and fields: still served, and saying so.

A route or field a live pod calls is never removed in the change that replaces
it. Apps, functions and agents in existing pods call the shapes they were
written against, and deleting one breaks every pod that used it at once. So
the old shape keeps working -- on top of the new mechanism -- and every
response to it carries the replacement, for the caller (an SDK warns, an agent
reads it and updates its code) and for us (each call is logged, so the route is
deleted once nobody calls it).

Three signals, all on the response:

- ``Deprecation: true`` (RFC 9745) and ``Sunset`` (RFC 8594), for generic
  clients;
- ``X-Lemma-Deprecation``: one line saying what to call instead, which the
  Lemma SDKs surface as a warning;
- ``deprecated: true`` on the operation in the OpenAPI spec, with the
  replacement in ``x-lemma-replacement``, so generated clients mark it too.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from email.utils import format_datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, Request, Response

from app.core.log.log import get_logger

logger = get_logger(__name__)

DEPRECATION_HEADER = "X-Lemma-Deprecation"


def _sunset_header(sunset: date) -> str:
    midnight = datetime.combine(sunset, time.min, tzinfo=timezone.utc)
    return format_datetime(midnight, usegmt=True)


def _actor_kind(request: Request) -> str:
    """Who is still calling: a person, a function run, or a delegated workload."""
    if getattr(request.state, "function_run_claims", None) is not None:
        return "function"
    claims = getattr(request.state, "delegation_claims", None)
    if claims is not None:
        # ``DelegationClaims.actor_type``: AGENT or FUNCTION. It used to read
        # a ``workload_type`` the claims never had, so every delegated caller
        # was logged as "workload" and the log could not say which to chase.
        kind = getattr(claims, "actor_type", None)
        return str(getattr(kind, "value", kind) or "workload").lower()
    return "person"


def _pod_id(request: Request) -> UUID | None:
    for name in ("function_run_claims", "delegation_claims"):
        pod_id = getattr(getattr(request.state, name, None), "pod_id", None)
        if pod_id is not None:
            return pod_id
    raw = request.path_params.get("pod_id")
    return UUID(str(raw)) if raw else None


def note_deprecated_use(
    request: Request,
    response: Response,
    *,
    use: str,
    replacement: str,
    sunset: date,
) -> None:
    """Mark one response as having served a deprecated route or field.

    ``use`` names what was deprecated (an operation id, or ``op.field``);
    ``replacement`` is the sentence a caller acts on.
    """
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = _sunset_header(sunset)
    response.headers[DEPRECATION_HEADER] = f"{use} is deprecated: {replacement}"
    logger.info(
        "api.deprecated_call",
        deprecated_use=use,
        actor_kind=_actor_kind(request),
        pod_id=str(pod_id) if (pod_id := _pod_id(request)) else None,
    )


def deprecated_route(
    *, operation_id: str, replacement: str, sunset: date
) -> dict[str, Any]:
    """Route keyword arguments for a deprecated operation.

    ``@router.post(path, **deprecated_route(...))`` marks the operation in the
    spec and sets the response headers on every call it serves.
    """

    async def _mark(request: Request, response: Response) -> None:
        note_deprecated_use(
            request, response, use=operation_id, replacement=replacement, sunset=sunset
        )

    return {
        "operation_id": operation_id,
        "deprecated": True,
        "openapi_extra": {
            "x-lemma-replacement": replacement,
            "x-lemma-sunset": sunset.isoformat(),
        },
        "dependencies": [Depends(_mark)],
    }
