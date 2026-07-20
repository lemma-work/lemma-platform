from __future__ import annotations

import json
from typing import Any

from starlette.responses import JSONResponse

from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.services.auth_abuse import (
    AltchaRejected,
    RateLimitExceeded,
    client_ip,
    get_auth_abuse_store,
)


_EMAIL_ENDPOINTS = {
    "/auth/signup": "signup",
    "/auth/user/email/verify/token": "verification",
    "/auth/user/password/reset/token": "password-reset",
}


def _email_from_body(payload: dict[str, Any]) -> str | None:
    fields = payload.get("formFields")
    if not isinstance(fields, list):
        return None
    for field in fields:
        if isinstance(field, dict) and field.get("id") == "email":
            value = field.get("value")
            if not value:
                return None
            try:
                return normalize_identity_email(str(value))
            except ValueError:
                return None
    return None


class AuthAbuseMiddleware:
    """Rate-limit and proof-gate the mounted SuperTokens HTTP API."""

    def __init__(self, app, auth_paths_only: bool = False):
        self.app = app
        self.auth_paths_only = auth_paths_only

    @staticmethod
    async def _body(receive) -> tuple[bytes, Any]:
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        return body, replay

    @staticmethod
    async def _reject(
        scope, receive, send, status: int, detail: str, retry: int | None = None
    ):
        headers = {"Retry-After": str(retry)} if retry is not None else None
        response = JSONResponse(
            {"status": "GENERAL_ERROR", "message": detail},
            status_code=status,
            headers=headers,
        )
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if self.auth_paths_only and not path.startswith("/auth/"):
            await self.app(scope, receive, send)
            return
        # Starlette retains the mount prefix in ``path`` and exposes the same
        # prefix as ``root_path``. Normalise it so this middleware applies to
        # the production ``/st`` mount as well as to the auth app in isolation.
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :] or "/"
        ip = client_ip(scope)
        store = get_auth_abuse_store()
        ip_hash = store.digest(ip)
        body = b""
        payload: dict[str, Any] = {}
        if scope.get("method") == "POST" and path in {
            *_EMAIL_ENDPOINTS,
            "/auth/signin",
        }:
            body, receive = await self._body(receive)
            try:
                parsed = json.loads(body or b"{}")
                payload = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                payload = {}
        email = _email_from_body(payload)
        email_hash = store.digest(email) if email else None

        try:
            await store.enforce(
                f"identity:rate:global:{ip_hash}", limit=60, window_seconds=60
            )
            if path in _EMAIL_ENDPOINTS:
                await store.enforce(
                    f"identity:rate:email-action:ip:15m:{ip_hash}",
                    limit=5,
                    window_seconds=900,
                )
                await store.enforce(
                    f"identity:rate:email-action:ip:day:{ip_hash}",
                    limit=20,
                    window_seconds=86_400,
                )
                if email_hash:
                    await store.enforce(
                        f"identity:rate:email-action:email:15m:{email_hash}",
                        limit=3,
                        window_seconds=900,
                    )
                    await store.enforce(
                        f"identity:rate:email-action:email:day:{email_hash}",
                        limit=6,
                        window_seconds=86_400,
                    )
                headers = {
                    key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in scope.get("headers", [])
                }
                await store.verify_altcha(
                    headers.get("x-altcha-payload"), purpose=_EMAIL_ENDPOINTS[path]
                )
            if path == "/auth/signin":
                failure_ip_key = f"identity:rate:signin-failure:ip:{ip_hash}"
                failure_pair_key = (
                    f"identity:rate:signin-failure:pair:{ip_hash}:{email_hash}"
                    if email_hash
                    else None
                )
                ip_failures = await store.count(failure_ip_key)
                pair_failures = (
                    await store.count(failure_pair_key) if failure_pair_key else 0
                )
                if ip_failures >= 20 or pair_failures >= 5:
                    raise RateLimitExceeded(900)
                if ip_failures >= 10 or pair_failures >= 3:
                    headers = {
                        key.decode("latin-1").lower(): value.decode("latin-1")
                        for key, value in scope.get("headers", [])
                    }
                    await store.verify_altcha(
                        headers.get("x-altcha-payload"), purpose="signin-risk"
                    )
        except RateLimitExceeded as exc:
            await self._reject(
                scope,
                receive,
                send,
                429,
                "Too many authentication attempts",
                exc.retry_after_seconds,
            )
            return
        except AltchaRejected as exc:
            await self._reject(scope, receive, send, 400, str(exc))
            return

        response_chunks: list[bytes] = []

        async def capture_send(message):
            if message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, capture_send)

        if path == "/auth/signin" and response_chunks:
            try:
                response_payload = json.loads(b"".join(response_chunks))
            except json.JSONDecodeError, UnicodeDecodeError:
                return
            failure_ip_key = f"identity:rate:signin-failure:ip:{ip_hash}"
            failure_pair_key = (
                f"identity:rate:signin-failure:pair:{ip_hash}:{email_hash}"
                if email_hash
                else None
            )
            if response_payload.get("status") == "WRONG_CREDENTIALS_ERROR":
                await store.enforce(failure_ip_key, limit=20, window_seconds=900)
                if failure_pair_key:
                    await store.enforce(failure_pair_key, limit=5, window_seconds=900)
            elif response_payload.get("status") == "OK" and failure_pair_key:
                await store.clear(failure_pair_key)
