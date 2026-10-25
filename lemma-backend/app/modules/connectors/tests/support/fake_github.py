"""GitHub, answered at the HTTP boundary.

Installed in front of httpx's network transport, so the code under test runs
whole -- the OAuth exchange (authlib's client is an ``httpx.AsyncClient``),
`/user/installations`, the per-installation check, `/user` and
`/user/memberships/orgs/{org}` -- and only the network is stood in for.
Anything not addressed to GitHub goes through untouched, the in-process API
client included.

The world is people and installations. A person is who a token speaks for;
the OAuth exchange hands out ``gho_<code>`` for a callback's ``code``, so a test
picks the GitHub user a callback comes back as by the code it sends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

HOSTS = frozenset({"github.com", "api.github.com"})


@dataclass
class GitHubUser:
    id: int
    login: str
    #: Installation ids this user can see a repository in.
    sees: set[str] = field(default_factory=set)
    #: GitHub organizations this user is an admin of, and a plain member of.
    admin_of: set[str] = field(default_factory=set)
    member_of: set[str] = field(default_factory=set)


@dataclass
class FakeGitHub:
    users: dict[str, GitHubUser] = field(default_factory=dict)
    installations: dict[str, dict[str, Any]] = field(default_factory=dict)
    _next_id: int = 1000

    def person(self, code: str, **kwargs: Any) -> str:
        """A GitHub user, reached by the token a callback with ``code`` gets.
        Returns that token."""
        self._next_id += 1
        token = f"gho_{code}"
        self.users[token] = GitHubUser(
            id=self._next_id, login=kwargs.pop("login", code), **kwargs
        )
        return token

    def install(
        self, installation_id: str, *, on: str, account_type: str = "Organization"
    ) -> None:
        """The App installed on GitHub account ``on`` (a login)."""
        owner = next((u for u in self.users.values() if u.login == on), None)
        self.installations[installation_id] = {
            "id": int(installation_id),
            "account": {
                "login": on,
                "type": account_type,
                "id": owner.id if owner and account_type == "User" else 9_000_000,
            },
            "repository_selection": "selected",
        }

    def answer(
        self,
        *,
        method: str,
        host: str,
        path: str,
        query: dict[str, str],
        body: bytes,
        authorization: str,
    ) -> tuple[int, dict[str, Any]]:
        """Status and JSON body for one request to GitHub."""
        if host == "github.com":
            code = (parse_qs(body.decode()).get("code") or [""])[0]
            token = {"access_token": f"gho_{code}", "token_type": "bearer"}
            return 200, {**token, "scope": ""}
        user = self.users.get(authorization.removeprefix("Bearer "))
        if user is None:
            return 401, {}
        if path == "/user":
            return 200, {"id": user.id, "login": user.login}
        if path == "/user/installations":
            first_page = query.get("page", "1") == "1"
            listed = [
                self.installations[one]
                for one in sorted(user.sees)
                if one in self.installations and first_page
            ]
            return 200, {"total_count": len(listed), "installations": listed}
        if path.startswith("/user/installations/") and path.endswith("/repositories"):
            reachable = path.split("/")[3] in user.sees
            return (200 if reachable else 404), {"repositories": []}
        if path.startswith("/user/memberships/orgs/"):
            organization = path.rsplit("/", 1)[1]
            if organization in user.admin_of:
                return 200, {"role": "admin", "state": "active"}
            if organization in user.member_of:
                return 200, {"role": "member", "state": "active"}
        return 404, {}


def _stand_in_front(monkeypatch: Any, library: Any, fake: FakeGitHub) -> None:
    real = library.AsyncHTTPTransport.handle_async_request

    async def handle(self: Any, request: Any) -> Any:
        if request.url.host not in HOSTS:
            return await real(self, request)
        body = await request.aread()
        status, payload = fake.answer(
            method=request.method,
            host=request.url.host,
            path=request.url.path,
            query=dict(request.url.params),
            body=body,
            authorization=request.headers.get("Authorization") or "",
        )
        return library.Response(status, json=payload, request=request)

    monkeypatch.setattr(library.AsyncHTTPTransport, "handle_async_request", handle)


def answer_github(monkeypatch: Any) -> FakeGitHub:
    """Stand ``FakeGitHub`` in front of every outbound call to GitHub.

    At the transport, not the client, and for both HTTP libraries in the
    process: the REST calls go out through ``httpx``, while authlib's OAuth
    client -- which exchanges the code -- is built on ``httpx2``. Every client
    without a transport of its own ends at these; the in-process API client
    has an ASGI transport and never does.
    """
    import httpx2

    fake = FakeGitHub()
    _stand_in_front(monkeypatch, httpx, fake)
    _stand_in_front(monkeypatch, httpx2, fake)
    return fake
