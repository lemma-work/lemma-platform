"""How a Kreuzberg failure is classified: this document's fault, or Kreuzberg's.

Split from ``kreuzberg_helper`` to keep it under the size ratchet. The rule:
a 5xx, 408, 413 or timeout on one document is the document's failure when
Kreuzberg's own health check passes, and Kreuzberg being down when it fails.
Connection refused is always down; 429 is busy. Only "down" and "busy" are
waited out without charging the document an attempt.
"""

import asyncio

import aiohttp

from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import (
    DocumentExtractionUnavailableError,
    ExtractorFailedOnDocument,
)
from app.modules.datastore.infrastructure.kreuzberg_circuit import (
    get_kreuzberg_circuit,
)

logger = get_logger(__name__)


class KreuzbergUnreachable(DocumentExtractionUnavailableError):
    """The connection to Kreuzberg failed after the in-request retries."""

    evidence = "connection_refused"


class KreuzbergUnhealthy(DocumentExtractionUnavailableError):
    """Kreuzberg failed a request and then failed its own health check."""

    evidence = "health_check_failing"


class KreuzbergRateLimited(DocumentExtractionUnavailableError):
    """Kreuzberg said 429: it is busy, not broken, and not this document's fault."""

    evidence = "rate_limited"


class _DocumentLevelFailure(Exception):
    """A 5xx, 408 or 413 on this request, before the health check decides whose."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class KreuzbergCompatibilityError(RuntimeError):
    """The extractor rejected the request schema and a legacy config may work."""


async def judge_document_failure(
    session: aiohttp.ClientSession, base_url: str | None, message: str
) -> Exception:
    """Whose failure a 5xx, timeout or 413 is: this document's, or Kreuzberg's.

    Asked once, of Kreuzberg itself. A passing health check means it is
    serving others, so the document is charged an attempt and counted
    (once) against it in the breaker; a failing one means it is down, and
    waiting is not charged.
    """
    if await _is_healthy(session, base_url):
        get_kreuzberg_circuit().record_failure()
        return ExtractorFailedOnDocument(message)
    get_kreuzberg_circuit().record_failure(document_key=None)
    return KreuzbergUnhealthy(f"{message}; Kreuzberg health check failing")


async def _is_healthy(session: aiohttp.ClientSession, base_url: str | None) -> bool:
    timeout = aiohttp.ClientTimeout(
        total=datastore_settings.kreuzberg_health_timeout_seconds
    )
    try:
        async with session.get(f"{base_url}/health", timeout=timeout) as response:
            return response.status < 500
    except aiohttp.ClientError, asyncio.TimeoutError, TimeoutError:
        return False


async def raise_for_status(response: aiohttp.ClientResponse) -> None:
    if response.status < 400:
        return
    body = await response.text()
    logger.debug(
        "datastore.kreuzberg_helper.extract_rejected.diagnostic",
        status=response.status,
        body_bytes=len(body),
    )
    if response.status in {400, 422}:
        # 400 = bad request/config (the compat-config ladder may recover it);
        # 422 = the document itself could not be parsed or OCR'd.
        raise KreuzbergCompatibilityError(
            f"Extractor request failed with status {response.status}"
        )
    # The body stays out of everything raised from here on: these messages
    # reach the work item's ``last_error``, and a provider's body can carry
    # anything, credentials included.
    summary = f"Extractor request failed with status {response.status}"
    if response.status == 429:
        retry_after = response.headers.get("Retry-After")
        raise KreuzbergRateLimited(
            summary,
            retry_after_seconds=float(retry_after)
            if retry_after and retry_after.isdigit()
            else None,
        )
    if response.status in {408, 413} or response.status >= 500:
        # A per-file extraction timeout arrives as a 500 carrying
        # error_type=TimeoutError, a panic on one document as a plain 500,
        # and a cold container's failed model download as a 502. The status
        # cannot say whether the document or the extractor is at fault;
        # the health check asked by the caller can.
        raise _DocumentLevelFailure(summary)
    raise RuntimeError(summary)
