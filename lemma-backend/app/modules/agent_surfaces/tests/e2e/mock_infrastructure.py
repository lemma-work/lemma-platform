"""Port/adapter abstractions and mock implementations for e2e tests.

These allow testing the full surface webhook flow without real external
platform API calls. Mock servers simulate platform APIs (Slack, Teams,
WhatsApp, Telegram) and capture outbound messages for assertion.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from contextlib import suppress
from typing import Any
import jwt
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from app.core.log.log import get_logger

logger = get_logger(__name__)


class MockPlatformMessageStore:
    """Thread-safe store for messages sent via mock platform servers."""

    def __init__(self) -> None:
        self._messages: dict[str, list[dict]] = {}

    def add(self, platform: str, message: dict) -> None:
        self._messages.setdefault(platform, []).append(message)

    def get_all(self, platform: str) -> list[dict]:
        return list(self._messages.get(platform, []))

    def clear(self) -> None:
        self._messages.clear()


class FakeSlackServer:
    """Lightweight aiohttp server mimicking the Slack Web API."""

    def __init__(self, test_user_email: str, store: MockPlatformMessageStore):
        self._test_user_email = test_user_email
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/api/users.info", self._users_info)
        app.router.add_route(
            "*", "/api/conversations.history", self._conversations_history
        )
        app.router.add_route(
            "*", "/api/conversations.replies", self._conversations_replies
        )
        app.router.add_route("*", "/api/chat.postMessage", self._chat_post_message)
        app.router.add_route("*", "/api/chat.update", self._chat_update)
        app.router.add_route("*", "/api/chat.delete", self._chat_delete)
        app.router.add_route("*", "/api/reactions.add", self._reactions_add)
        app.router.add_route(
            "*", "/api/assistant.threads.setStatus", self._assistant_threads_set_status
        )
        app.router.add_route(
            "*", "/api/files.getUploadURLExternal", self._files_get_upload_url_external
        )
        app.router.add_route(
            "*", "/api/files.completeUploadExternal", self._files_complete_upload_external
        )
        app.router.add_post("/upload/{file_id}", self._upload_raw_file)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        self._port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/api/"

    async def _collect_params(self, request: web.Request) -> dict[str, str]:
        payload = dict(request.query)
        if request.can_read_body:
            with suppress(Exception):
                data = await request.json()
                if isinstance(data, dict):
                    payload.update(
                        {k: str(v) for k, v in data.items() if v is not None}
                    )
            with suppress(Exception):
                form = await request.post()
                payload.update({k: str(v) for k, v in form.items()})
        return payload

    async def _users_info(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        return web.json_response(
            {
                "ok": True,
                "user": {
                    "id": params.get("user"),
                    "profile": {
                        "email": self._test_user_email,
                        "display_name": "Surface Test User",
                    },
                },
            }
        )

    async def _conversations_history(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "messages": []})

    async def _conversations_replies(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "messages": []})

    async def _chat_post_message(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK", params)
        ts = f"1700000000.{len(self._store.get_all('SLACK')):06d}"
        return web.json_response(
            {"ok": True, "ts": ts, "channel": params.get("channel")}
        )

    async def _chat_update(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_UPDATE", params)
        return web.json_response(
            {"ok": True, "ts": params.get("ts"), "channel": params.get("channel")}
        )

    async def _chat_delete(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_DELETE", params)
        return web.json_response({"ok": True, "ts": params.get("ts")})

    async def _reactions_add(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_REACTIONS", params)
        return web.json_response({"ok": True})

    async def _assistant_threads_set_status(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_STATUS", params)
        return web.json_response({"ok": True})

    async def _files_get_upload_url_external(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        file_id = f"F{len(self._store.get_all('SLACK_FILE_UPLOAD')) + 1:09d}"
        self._store.add("SLACK_FILE_UPLOAD_URL", params)
        return web.json_response(
            {
                "ok": True,
                "upload_url": f"http://127.0.0.1:{self._port}/upload/{file_id}",
                "file_id": file_id,
            }
        )

    async def _upload_raw_file(self, request: web.Request) -> web.Response:
        form = await request.post()
        uploaded = form.get("file")
        self._store.add(
            "SLACK_FILE_UPLOAD",
            {
                "file_id": request.match_info["file_id"],
                "filename": getattr(uploaded, "filename", None),
                "size": len(uploaded.file.read()) if hasattr(uploaded, "file") else None,
            },
        )
        return web.Response(text="OK")

    async def _files_complete_upload_external(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_FILE_COMPLETE", params)
        return web.json_response({"ok": True, "files": []})


class FakeTeamsServer:
    """Lightweight aiohttp server mimicking the MS Teams Bot Framework."""

    def __init__(self, test_user_email: str, store: MockPlatformMessageStore):
        self._test_user_email = test_user_email
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self._private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self._public_jwk = json.loads(
            RSAAlgorithm.to_jwk(self._private_key.public_key())
        )
        self._kid = "fake-teams-key-1"
        self._public_jwk["kid"] = self._kid

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get(
            "/botframework/.well-known/openidconfiguration",
            self._openid_configuration,
        )
        app.router.add_get("/botframework/keys", self._jwks)
        app.router.add_post(
            "/teams/v3/conversations/{conversation_id}/activities",
            self._post_activity,
        )
        app.router.add_put(
            "/teams/v3/conversations/{conversation_id}/activities/{activity_id}",
            self._put_activity,
        )
        app.router.add_get(
            "/teams/v3/conversations/{conversation_id}/members/{member_id}",
            self._get_member,
        )
        app.router.add_get("/teams/v3/teams/{team_id}", self._get_team)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        self._port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def service_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/teams"

    @property
    def openid_config_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/botframework/.well-known/openidconfiguration"

    def issue_webhook_token(self, *, audience: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": "https://api.botframework.com",
                "aud": audience,
                "iat": now,
                "nbf": now - 10,
                "exp": now + 600,
            },
            self._private_key,
            algorithm="RS256",
            headers={"kid": self._kid},
        )

    async def _openid_configuration(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {"jwks_uri": f"http://127.0.0.1:{self._port}/botframework/keys"}
        )

    async def _jwks(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"keys": [self._public_jwk]})

    async def _post_activity(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add("TEAMS", {"path": str(request.rel_url), "body": body})
        return web.json_response(
            {"id": f"activity-{len(self._store.get_all('TEAMS'))}"}
        )

    async def _put_activity(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "TEAMS_UPDATE",
            {
                "path": str(request.rel_url),
                "activity_id": request.match_info["activity_id"],
                "body": body,
            },
        )
        return web.json_response({"id": request.match_info["activity_id"]})

    async def _get_member(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": request.match_info["member_id"],
                "name": "Surface Test User",
                "userPrincipalName": self._test_user_email,
            }
        )

    async def _get_team(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "id": request.match_info["team_id"],
                "name": "Surface Test Team",
                "aadGroupId": "11111111-2222-4333-8444-555555555555",
            }
        )


class FakeWhatsAppServer:
    """Lightweight aiohttp server mimicking the WhatsApp Business API."""

    def __init__(self, store: MockPlatformMessageStore):
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post(
            "/v21.0/{phone_number_id}/messages",
            self._send_message,
        )
        app.router.add_post(
            "/v21.0/{phone_number_id}/media",
            self._upload_media,
        )

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        self._port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def _upload_media(self, request: web.Request) -> web.Response:
        form = await request.post()
        uploaded = form.get("file")
        media_id = f"media-{len(self._store.get_all('WHATSAPP_MEDIA_UPLOAD')) + 1}"
        self._store.add(
            "WHATSAPP_MEDIA_UPLOAD",
            {
                "phone_number_id": request.match_info["phone_number_id"],
                "filename": getattr(uploaded, "filename", None),
                "media_id": media_id,
            },
        )
        return web.json_response({"id": media_id})

    async def _send_message(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add("WHATSAPP", body)
        return web.json_response(
            {
                "messaging_product": "whatsapp",
                "contacts": [{"input": body.get("to"), "wa_id": body.get("to")}],
                "messages": [{"id": f"wamid.{len(self._store.get_all('WHATSAPP'))}"}],
            }
        )


class FakeTelegramServer:
    """Lightweight aiohttp server mimicking the Telegram Bot API.

    Captures outbound calls for assertion and remembers the registered webhook
    so getWebhookInfo confirms the URL (matching the real registration flow).
    ``fail_next`` forces transient failures per method to exercise retries.
    """

    def __init__(self, store: MockPlatformMessageStore):
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self._registered_webhook: dict | None = None
        self.fail_next: dict[str, int] = {}

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/bot{token}/sendMessage", self._send_message)
        app.router.add_post("/bot{token}/editMessageText", self._edit_message_text)
        app.router.add_post("/bot{token}/sendVoice", self._send_voice)
        app.router.add_post("/bot{token}/sendDocument", self._send_document)
        app.router.add_post("/bot{token}/sendChatAction", self._send_chat_action)
        app.router.add_post("/bot{token}/getMe", self._get_me)
        app.router.add_post("/bot{token}/setWebhook", self._set_webhook)
        app.router.add_post("/bot{token}/deleteWebhook", self._delete_webhook)
        app.router.add_post("/bot{token}/getWebhookInfo", self._get_webhook_info)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        self._port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def webhook_calls(self) -> list[str]:
        """Ordered list of webhook lifecycle methods (e.g. delete then set)."""
        return [entry["method"] for entry in self._store.get_all("TELEGRAM_WEBHOOK")]

    def _maybe_fail(self, method: str) -> web.Response | None:
        remaining = self.fail_next.get(method, 0)
        if remaining > 0:
            self.fail_next[method] = remaining - 1
            return web.json_response(
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry later",
                    "parameters": {"retry_after": 0},
                },
                status=429,
            )
        return None

    async def _send_message(self, request: web.Request) -> web.Response:
        failure = self._maybe_fail("sendMessage")
        if failure is not None:
            return failure
        body = await request.json()
        text = body.get("text") or ""
        if len(text) > 4096:
            return web.json_response(
                {"ok": False, "error_code": 400, "description": "Bad Request: message is too long"},
                status=400,
            )
        self._store.add("TELEGRAM", body)
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "message_id": len(self._store.get_all("TELEGRAM")),
                    "chat": {"id": body.get("chat_id")},
                    "text": text,
                },
            }
        )

    async def _edit_message_text(self, request: web.Request) -> web.Response:
        failure = self._maybe_fail("editMessageText")
        if failure is not None:
            return failure
        body = await request.json()
        self._store.add("TELEGRAM_EDIT", body)
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "message_id": body.get("message_id"),
                    "chat": {"id": body.get("chat_id")},
                    "text": body.get("text"),
                },
            }
        )

    async def _send_voice(self, request: web.Request) -> web.Response:
        # sendVoice is a multipart upload (data fields + the OGG voice file part).
        form = await request.post()
        voice = form.get("voice")
        self._store.add(
            "TELEGRAM_VOICE",
            {
                "chat_id": str(form.get("chat_id")) if form.get("chat_id") else None,
                "caption": str(form.get("caption")) if form.get("caption") else None,
                "has_voice": voice is not None,
                "voice_filename": getattr(voice, "filename", None),
            },
        )
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "message_id": len(self._store.get_all("TELEGRAM_VOICE")),
                    "voice": {"file_id": "voice-file-1"},
                },
            }
        )

    async def _send_document(self, request: web.Request) -> web.Response:
        form = await request.post()
        document = form.get("document")
        self._store.add(
            "TELEGRAM_FILE",
            {
                "chat_id": str(form.get("chat_id")) if form.get("chat_id") else None,
                "caption": str(form.get("caption")) if form.get("caption") else None,
                "filename": getattr(document, "filename", None),
            },
        )
        return web.json_response(
            {
                "ok": True,
                "result": {"message_id": len(self._store.get_all("TELEGRAM_FILE"))},
            }
        )

    async def _send_chat_action(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _get_me(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "id": 12345,
                    "is_bot": True,
                    "first_name": "LemmaBot",
                    "username": "lemmabot",
                },
            }
        )

    async def _set_webhook(self, request: web.Request) -> web.Response:
        failure = self._maybe_fail("setWebhook")
        if failure is not None:
            return failure
        body = await request.json()
        self._registered_webhook = body
        self._store.add(
            "TELEGRAM_WEBHOOK",
            {
                "method": "setWebhook",
                "token": request.match_info["token"],
                "body": body,
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _delete_webhook(self, request: web.Request) -> web.Response:
        failure = self._maybe_fail("deleteWebhook")
        if failure is not None:
            return failure
        body = await request.json()
        self._registered_webhook = None
        self._store.add(
            "TELEGRAM_WEBHOOK",
            {
                "method": "deleteWebhook",
                "token": request.match_info["token"],
                "body": body,
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _get_webhook_info(self, request: web.Request) -> web.Response:
        url = (self._registered_webhook or {}).get("url", "")
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "url": url,
                    "has_custom_certificate": False,
                    "pending_update_count": 0,
                },
            }
        )


class FakeGmailServer:
    """Lightweight aiohttp server mimicking the Gmail send API."""

    def __init__(self, store: MockPlatformMessageStore):
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/gmail/v1/users/me/messages/send", self._send_message)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        self._port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def _send_message(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add("GMAIL", body)
        return web.json_response({"id": "gmail-message-1"})


class FakeResendServer:
    """Lightweight aiohttp server mimicking the Resend send API."""

    def __init__(self, store: MockPlatformMessageStore):
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/emails", self._send_email)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        self._port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def _send_email(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add("RESEND", body)
        return web.json_response({"id": f"resend-message-{len(self._store.get_all('RESEND'))}"})


class FakeOutlookServer:
    """Lightweight aiohttp server mimicking Outlook Graph message APIs."""

    def __init__(self, store: MockPlatformMessageStore):
        self._store = store
        self._messages_by_id: dict[str, dict[str, Any]] = {}
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v1.0/me/messages/{message_id}", self._get_message)
        app.router.add_post("/v1.0/me/messages/{message_id}/reply", self._reply)
        app.router.add_post(
            "/v1.0/me/messages/{message_id}/createReply",
            self._create_reply,
        )
        app.router.add_patch("/v1.0/me/messages/{message_id}", self._update_message)
        app.router.add_post(
            "/v1.0/me/messages/{message_id}/attachments",
            self._add_attachment,
        )
        app.router.add_post("/v1.0/me/messages/{message_id}/send", self._send_draft)
        app.router.add_post("/v1.0/me/sendMail", self._send_mail)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        self._port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def set_message(self, message_id: str, payload: dict[str, Any]) -> None:
        self._messages_by_id[message_id] = payload

    async def _get_message(self, request: web.Request) -> web.Response:
        message_id = request.match_info["message_id"]
        payload = self._messages_by_id.get(message_id)
        if payload is None:
            return web.json_response({"error": {"message": "Not found"}}, status=404)
        self._store.add(
            "OUTLOOK_FETCH",
            {"message_id": message_id, "query": dict(request.query)},
        )
        return web.json_response(payload)

    async def _send_mail(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add("OUTLOOK", body)
        return web.Response(status=202)

    async def _reply(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "OUTLOOK_REPLY",
            {
                "message_id": request.match_info["message_id"],
                "body": body,
            },
        )
        return web.Response(status=202)

    async def _create_reply(self, request: web.Request) -> web.Response:
        draft_id = f"draft-{len(self._store.get_all('OUTLOOK_DRAFT_CREATE')) + 1}"
        self._store.add(
            "OUTLOOK_DRAFT_CREATE",
            {
                "source_message_id": request.match_info["message_id"],
                "draft_id": draft_id,
            },
        )
        return web.json_response({"id": draft_id})

    async def _update_message(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "OUTLOOK_DRAFT_PATCH",
            {
                "message_id": request.match_info["message_id"],
                "body": body,
            },
        )
        return web.Response(status=200)

    async def _add_attachment(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "OUTLOOK_DRAFT_ATTACHMENT",
            {
                "message_id": request.match_info["message_id"],
                "body": body,
            },
        )
        return web.json_response(
            {"id": f"attachment-{len(self._store.get_all('OUTLOOK_DRAFT_ATTACHMENT'))}"}
        )

    async def _send_draft(self, request: web.Request) -> web.Response:
        self._store.add(
            "OUTLOOK_DRAFT_SEND",
            {"message_id": request.match_info["message_id"]},
        )
        return web.Response(status=202)


def build_slack_signature_headers(
    *,
    raw_body: bytes,
    signing_secret: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    ts = str(timestamp or int(time.time()))
    basestring = b"v0:" + ts.encode("utf-8") + b":" + raw_body
    signature = (
        "v0="
        + hmac.new(
            signing_secret.encode("utf-8"),
            basestring,
            hashlib.sha256,
        ).hexdigest()
    )
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": signature,
        "Content-Type": "application/json",
    }


def build_whatsapp_signature_headers(
    *,
    raw_body: bytes,
    app_secret: str,
) -> dict[str, str]:
    signature = (
        "sha256="
        + hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
    return {
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }


def build_telegram_secret_headers(secret: str) -> dict[str, str]:
    return {
        "X-Telegram-Bot-Api-Secret-Token": secret,
        "Content-Type": "application/json",
    }


async def wait_for_messages(
    store: MockPlatformMessageStore,
    platform: str,
    min_count: int = 1,
    timeout_seconds: float = 30.0,
) -> list[dict]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        messages = store.get_all(platform)
        if len(messages) >= min_count:
            return messages
        await asyncio.sleep(0.2)
    return store.get_all(platform)
