"""The address a request came from, as the auth rate limits count it.

Shared rather than re-derived so every per-IP limit in the backend agrees on
which proxies are trusted to name the client, and a forwarded header from
anybody else is ignored.
"""

from app.modules.identity.services.auth_abuse import client_ip

__all__ = ["client_ip"]
