"""Token strings: what they look like, how long they live, how they are stored.

Opaque rather than signed. Every MCP request already touches the database to
build the caller's authorization context, so one indexed digest lookup more
buys what a JWT cannot: revoking a grant ends its tokens on the next request,
not at the next expiry.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from app.modules.mcp_access.domain.entities import TokenKind

ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(days=30)
"""Sliding: every refresh rotates the token and restarts the clock, so a client
in regular use never has to send the person back through consent."""

_PREFIXES = {
    TokenKind.ACCESS: "lemma_mcp_at_",
    TokenKind.REFRESH: "lemma_mcp_rt_",
}


def mint(kind: TokenKind) -> str:
    """256 random bits behind a prefix that says what the string is.

    The prefix is what lets the pod MCP endpoint tell one of these from a Lemma
    session token without asking SuperTokens first, and what lets a secret
    scanner recognise a leaked one.
    """
    return _PREFIXES[kind] + secrets.token_urlsafe(32)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def looks_like(token: str, kind: TokenKind) -> bool:
    return token.startswith(_PREFIXES[kind])
