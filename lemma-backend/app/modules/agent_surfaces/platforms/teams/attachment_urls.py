"""Which hosts a Teams attachment URL may send our credentials to.

An attachment's ``download_url`` is not ours: it arrives on an inbound message
or as an argument to an agent tool call. Deciding what to do with it therefore
has to start from the host, because the download plan attaches a bot or Graph
token to the request, and both are tenant-wide credentials.
"""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

from app.core.net.domains import host_is_within, hostname_of

# Bot Framework serves attachments from regional hosts under these domains, and
# they are the only hosts the bot token may be sent to.
_BOT_ATTACHMENT_DOMAINS = ("trafficmanager.net", "botframework.com")
_SHAREPOINT_DOMAIN = "sharepoint.com"


def filename_from_url(url: str) -> str | None:
    candidate = Path(str(url).split("?")[0]).name.strip()
    return candidate or None


def is_sharepoint_url(url: str) -> bool:
    return host_is_within(hostname_of(url), _SHAREPOINT_DOMAIN)


def is_raw_sharepoint_document_url(url: str) -> bool:
    if not is_sharepoint_url(url):
        return False
    path = (urlparse(url).path or "").lower()
    return "/shared documents/" in path or "/sites/" in path


def looks_like_bot_attachment_url(url: str, *, extra_host: str = "") -> bool:
    """Whether this URL is a Bot Framework attachment the bot token may reach.

    The host is the security boundary here, not the path. A caller-chosen host
    that merely carries a ``/v3/attachments/`` path must not be enough to be
    handed the credential.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not hostname or "/v3/attachments/" not in (parsed.path or ""):
        return False
    if extra_host and hostname == extra_host:
        return True
    return any(host_is_within(hostname, domain) for domain in _BOT_ATTACHMENT_DOMAINS)


def encode_share_url(url: str) -> str:
    encoded = base64.b64encode(url.encode("utf-8")).decode("utf-8")
    return "u!" + encoded.rstrip("=").replace("/", "_").replace("+", "-")


def split_sharepoint_site_and_item_path(raw_path: str) -> tuple[str, str | None]:
    path = "/" + str(raw_path).lstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return "/", None
    if segments[0] in {"sites", "teams", "personal"} and len(segments) >= 3:
        return "/" + "/".join(segments[:2]), "/" + "/".join(segments[2:])
    if len(segments) >= 2:
        return "/", "/" + "/".join(segments)
    return "/", None
