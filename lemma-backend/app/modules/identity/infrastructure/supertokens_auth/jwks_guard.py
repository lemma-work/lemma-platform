"""Stop an unknown ``kid`` costing a blocking network fetch on every request.

``verify_auth`` is a global dependency, so every request to this process goes
through SuperTokens' ``get_session``. That reaches
``supertokens_python.recipe.session.jwks.get_latest_keys(config, kid)``, which:

* serves from a cache when the ``kid`` is one it knows,
* and otherwise performs a **synchronous** ``requests.get`` — on the event loop,
  inside a write lock that also excludes every other verification — with no
  negative cache, so the same unknown ``kid`` re-fetches every single time.

The ``kid`` is read out of the token header *before* any signature check
(``access_token.get_info_from_access_token``). So an unauthenticated client
sending forged tokens with random ``kid`` values makes this process do one
blocking HTTP round trip per request, serialised, on the loop. That is a remote
event-loop stall that costs the sender nothing.

This installs a **negative cache**: a ``kid`` that was just looked up and not found is refused
immediately, with no network call, until the TTL expires. Legitimate key
rotation still works — the first request after a rotation pays one fetch, and a
successful fetch clears the negative cache so a newly-published ``kid`` is
picked up at once.
The residual: a genuine rotation still costs one synchronous fetch on the loop,
bounded to once per TTL rather than once per request. Removing that entirely
means not calling SuperTokens' verifier synchronously, which is a larger change
than this one.

There is deliberately no start-up pre-warm. It would save exactly one fetch,
once, and cost a broad exception handler on the auth path plus a coupling
between start-up and the SuperTokens core being reachable — a bad trade for
something the first legitimate request already pays today.

Both call sites do ``from ... import get_latest_keys``, binding the function
into their own module namespace, so patching the defining module alone would
have no effect. The names are replaced where they are actually looked up.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

# kid -> monotonic deadline after which we will try the network again.
_unknown_kids: dict[str, float] = {}

_original_get_latest_keys: Any = None
_patched_modules: list[Any] = []


class UnknownJwksKeyError(Exception):
    """Raised for a ``kid`` we recently failed to find.

    Deliberately a plain ``Exception``, which is what the upstream code raises
    on the same condition — ``get_info_from_access_token`` turns any failure
    here into a rejected token, and imitating the existing shape keeps that
    behaviour identical.
    """


def _guarded_get_latest_keys(config: Any, kid: str | None = None) -> Any:
    ttl = settings.auth_jwks_unknown_kid_ttl_seconds
    now = time.monotonic()

    if kid is not None and ttl > 0:
        deadline = _unknown_kids.get(kid)
        if deadline is not None:
            if now < deadline:
                raise UnknownJwksKeyError("No matching JWKS found")
            del _unknown_kids[kid]

    # try/finally rather than `except Exception`: the upstream raises a bare
    # Exception on "no matching key", so there is no narrower type to name, and
    # we do not want to swallow or reclassify it -- only to notice.
    found = False
    try:
        keys = _original_get_latest_keys(config, kid)
        found = True
    finally:
        if not found and kid is not None and ttl > 0:
            # Bound the map: the sender chooses the ids, and an unbounded dict
            # would just move the damage from the loop to memory. Past the cap
            # we stop remembering rather than stop serving -- the fetch is what
            # the cap protects, and forgetting only costs an extra fetch later.
            if len(_unknown_kids) >= settings.auth_jwks_unknown_kid_cache_size:
                _unknown_kids.clear()
                logger.warning("identity.jwks_guard.unknown_kid_cache_full.degraded")
            _unknown_kids[kid] = now + ttl

    # A successful fetch may have introduced keys we previously rejected.
    if _unknown_kids:
        _unknown_kids.clear()
    return keys


def install_jwks_guard() -> None:
    """Replace ``get_latest_keys`` wherever it is looked up. Idempotent."""
    global _original_get_latest_keys

    if _original_get_latest_keys is not None:
        return

    try:
        from supertokens_python.recipe.session import access_token, jwks
    except ImportError:  # pragma: no cover - SuperTokens is a hard dependency
        logger.warning("identity.jwks_guard.install_failed.degraded")
        return

    _original_get_latest_keys = jwks.get_latest_keys
    targets: list[Any] = [jwks, access_token]
    try:
        from supertokens_python.recipe.oauth2provider import recipe_implementation

        targets.append(recipe_implementation)
    except ImportError:  # pragma: no cover - optional recipe
        pass

    for module in targets:
        if getattr(module, "get_latest_keys", None) is not None:
            module.get_latest_keys = _guarded_get_latest_keys
            _patched_modules.append(module)


def reset_jwks_guard_for_test() -> None:
    """Restore the original function and clear the negative cache."""
    global _original_get_latest_keys
    for module in _patched_modules:
        module.get_latest_keys = _original_get_latest_keys
    _patched_modules.clear()
    _original_get_latest_keys = None
    _unknown_kids.clear()
