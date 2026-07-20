from __future__ import annotations

import json
from typing import Any

from starlette.responses import JSONResponse

from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.services.auth_abuse import (
    AltchaRejected,
    AuthAbuseStore,
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


def _normalised_path(scope: dict[str, Any]) -> str:
    """Return the route path without a Starlette mount prefix."""
    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        return path[len(root_path) :] or "/"
    return path


def _request_headers(scope: dict[str, Any]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _signin_failure_keys(
    ip_hash: str, email_hash: str | None
) -> tuple[str, str | None]:
    return (
        f"identity:rate:signin-failure:ip:{ip_hash}",
        (
            f"identity:rate:signin-failure:pair:{ip_hash}:{email_hash}"
            if email_hash
            else None
        ),
    )


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

    async def _read_auth_payload(self, scope, path: str, receive):
        should_read = scope.get("method") == "POST" and path in {
            *_EMAIL_ENDPOINTS,
            "/auth/signin",
        }
        if not should_read:
            return {}, receive
        body, replay = await self._body(receive)
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError, UnicodeDecodeError:
            return {}, replay
        return (payload if isinstance(payload, dict) else {}), replay

    @staticmethod
    async def _enforce_email_action(
        store: AuthAbuseStore,
        *,
        path: str,
        scope,
        ip_hash: str,
        email_hash: str | None,
    ) -> None:
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
        await store.verify_altcha(
            _request_headers(scope).get("x-altcha-payload"),
            purpose=_EMAIL_ENDPOINTS[path],
        )

    @staticmethod
    async def _enforce_signin_risk(
        store: AuthAbuseStore,
        *,
        scope,
        ip_hash: str,
        email_hash: str | None,
    ) -> None:
        failure_ip_key, failure_pair_key = _signin_failure_keys(ip_hash, email_hash)
        ip_failures = await store.count(failure_ip_key)
        pair_failures = await store.count(failure_pair_key) if failure_pair_key else 0
        if ip_failures >= 20 or pair_failures >= 5:
            raise RateLimitExceeded(900)
        if ip_failures >= 10 or pair_failures >= 3:
            await store.verify_altcha(
                _request_headers(scope).get("x-altcha-payload"),
                purpose="signin-risk",
            )

    async def _enforce_request(
        self,
        store: AuthAbuseStore,
        *,
        path: str,
        scope,
        ip_hash: str,
        email_hash: str | None,
    ) -> None:
        await store.enforce(
            f"identity:rate:global:{ip_hash}", limit=60, window_seconds=60
        )
        if path in _EMAIL_ENDPOINTS:
            await self._enforce_email_action(
                store,
                path=path,
                scope=scope,
                ip_hash=ip_hash,
                email_hash=email_hash,
            )
        if path == "/auth/signin":
            await self._enforce_signin_risk(
                store,
                scope=scope,
                ip_hash=ip_hash,
                email_hash=email_hash,
            )

    async def _call_and_capture(self, scope, receive, send) -> bytes:
        response_chunks: list[bytes] = []

        async def capture_send(message):
            if message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive, capture_send)
        return b"".join(response_chunks)

    @staticmethod
    async def _record_signin_result(
        store: AuthAbuseStore,
        *,
        response_body: bytes,
        ip_hash: str,
        email_hash: str | None,
    ) -> None:
        if not response_body:
            return
        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError, UnicodeDecodeError:
            return
        failure_ip_key, failure_pair_key = _signin_failure_keys(ip_hash, email_hash)
        if payload.get("status") == "WRONG_CREDENTIALS_ERROR":
            await store.enforce(failure_ip_key, limit=20, window_seconds=900)
            if failure_pair_key:
                await store.enforce(failure_pair_key, limit=5, window_seconds=900)
        elif payload.get("status") == "OK" and failure_pair_key:
            await store.clear(failure_pair_key)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = _normalised_path(scope)
        if self.auth_paths_only and not path.startswith("/auth/"):
            await self.app(scope, receive, send)
            return
        store = get_auth_abuse_store()
        ip_hash = store.digest(client_ip(scope))
        payload, receive = await self._read_auth_payload(scope, path, receive)
        email = _email_from_body(payload)
        email_hash = store.digest(email) if email else None

        try:
            await self._enforce_request(
                store,
                path=path,
                scope=scope,
                ip_hash=ip_hash,
                email_hash=email_hash,
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

        response_body = await self._call_and_capture(scope, receive, send)
        if path == "/auth/signin":
            await self._record_signin_result(
                store,
                response_body=response_body,
                ip_hash=ip_hash,
                email_hash=email_hash,
            )
