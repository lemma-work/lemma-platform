"""HTTP validators and cache policy for datastore file responses."""

from __future__ import annotations


def quote_content_etag(content_sha256: str | None) -> str | None:
    if not content_sha256:
        return None
    return f'"{content_sha256.lower()}"'


def if_none_match_matches(if_none_match: str | None, etag: str | None) -> bool:
    """Apply RFC weak comparison for GET/HEAD conditional requests."""
    if not if_none_match or not etag:
        return False
    target = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        normalized = candidate.strip()
        if normalized == "*" or normalized.removeprefix("W/") == target:
            return True
    return False


def file_cache_headers(
    content_sha256: str | None,
    *,
    cache_control: str,
) -> dict[str, str]:
    headers = {"Cache-Control": cache_control}
    etag = quote_content_etag(content_sha256)
    if etag:
        headers["ETag"] = etag
    return headers
