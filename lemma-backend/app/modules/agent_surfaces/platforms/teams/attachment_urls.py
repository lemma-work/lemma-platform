"""Which hosts a Teams attachment or reply URL may send our credentials to.

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
# Where Bot Framework tells a bot to reply: the public cloud's regional
# ``smba.trafficmanager.net`` paths, and the per-cloud ``smba`` hosts of the
# US government clouds (GCC under teams.microsoft.com, GCC High and DoD under
# teams.microsoft.us).
_BOT_SERVICE_HOSTS = frozenset({"smba.trafficmanager.net"})
_BOT_SERVICE_DOMAINS = (
    "botframework.com",
    "botframework.azure.us",
    "teams.microsoft.com",
    "teams.microsoft.us",
)


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


def normalized_service_url(url: str) -> str:
    """A ``serviceUrl`` in the form two copies of it can be compared in.

    The token claim and the activity field name the same endpoint but may
    differ in a trailing slash or in the case of the host.
    """
    return str(url or "").strip().rstrip("/").lower()


def is_bot_framework_service_url(url: str, *, extra_host: str = "") -> bool:
    """Whether a reply endpoint may be handed the bot token.

    Every reply, card update and member lookup posts the deployment-wide bot
    token to the activity's ``serviceUrl``. Unlike the attachment check above,
    ``trafficmanager.net`` is not trusted wholesale: anyone can create an Azure
    Traffic Manager profile under it, and only Microsoft's ``smba`` host is a
    Bot Framework endpoint.
    """
    parsed = urlparse(str(url or ""))
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False
    if extra_host and hostname == extra_host.lower():
        return True
    if parsed.scheme != "https":
        return False
    return hostname in _BOT_SERVICE_HOSTS or any(
        host_is_within(hostname, domain) for domain in _BOT_SERVICE_DOMAINS
    )


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
