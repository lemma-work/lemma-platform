"""Port/adapter abstractions and mock implementations for e2e tests.

These allow testing the full surface webhook flow without real external
platform API calls. Mock servers simulate platform APIs (Slack, Teams,
WhatsApp, Telegram) and capture outbound messages for assertion.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any
import jwt
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from app.core.log.log import get_logger
from app.modules.test_support.e2e.waiters import eventually

logger = get_logger(__name__)


def _request_contract(request: web.Request) -> dict[str, str]:
    metadata = {
        "_method": request.method,
        "_path": str(request.rel_url),
    }
    authorization = request.headers.get("Authorization")
    if authorization:
        metadata["_authorization"] = authorization
    content_type = request.headers.get("Content-Type")
    if content_type:
        metadata["_content_type"] = content_type
    return metadata


class MockPlatformMessageStore:
    """Thread-safe store for messages sent via mock platform servers."""

    def __init__(self) -> None:
        self._messages: dict[str, list[dict]] = {}
        # One reply can reach a platform by more than one call — Slack posts a
        # plain message but streams an answer — so "what did the user see, and
        # in what order?" cannot be answered from a single bucket. The arrival
        # log keeps that order across buckets.
        self._arrivals: list[tuple[str, dict]] = []

    def add(self, platform: str, message: dict) -> None:
        self._messages.setdefault(platform, []).append(message)
        self._arrivals.append((platform, message))

    def get_all(self, platform: str) -> list[dict]:
        return list(self._messages.get(platform, []))

    def get_ordered(self, *platforms: str) -> list[tuple[str, dict]]:
        """Messages across the given buckets, in the order they arrived."""
        wanted = set(platforms)
        return [
            (platform, message)
            for platform, message in self._arrivals
            if not wanted or platform in wanted
        ]

    def clear(self) -> None:
        self._messages.clear()
        self._arrivals.clear()


class FakeComposioServer:
    """Hermetic Composio v3.1 tool transport used by email-surface workers."""

    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self.executions: list[dict[str, Any]] = []
        self.outlook_messages: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/v3.1/tools/{tool_slug}", self._retrieve_tool)
        app.router.add_post(
            "/api/v3.1/tools/execute/{tool_slug}",
            self._execute_tool,
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
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def set_outlook_message(self, message_id: str, payload: dict[str, Any]) -> None:
        self.outlook_messages[message_id] = payload

    async def _retrieve_tool(self, request: web.Request) -> web.Response:
        slug = request.match_info["tool_slug"]
        toolkit_slug = "outlook" if slug.startswith("OUTLOOK_") else "gmail"
        return web.json_response(
            {
                "available_versions": ["latest"],
                "deprecated": {
                    "available_versions": ["latest"],
                    "displayName": slug,
                    "is_deprecated": False,
                    "toolkit": {"logo": ""},
                    "version": "latest",
                },
                "description": f"Hermetic {slug}",
                "input_parameters": {},
                "is_deprecated": False,
                "name": slug,
                "no_auth": False,
                "output_parameters": {},
                "scopes": [],
                "slug": slug,
                "tags": [],
                "toolkit": {
                    "logo": "",
                    "name": toolkit_slug.title(),
                    "slug": toolkit_slug,
                },
                "version": "latest",
            }
        )

    async def _execute_tool(self, request: web.Request) -> web.Response:
        body = await request.json()
        tool_slug = request.match_info["tool_slug"]
        execution = {
            "tool_slug": tool_slug,
            "body": body,
            **_request_contract(request),
        }
        self.executions.append(execution)
        arguments = body.get("arguments") or {}
        if tool_slug == "OUTLOOK_GET_MESSAGE":
            data = self.outlook_messages.get(str(arguments.get("message_id") or ""), {})
        elif "ATTACHMENT" in tool_slug:
            data = {
                "content_b64": base64.b64encode(
                    f"fake attachment from {tool_slug}".encode()
                ).decode("ascii")
            }
        else:
            data = {
                "id": f"composio-e2e-{len(self.executions)}",
                "thread_id": arguments.get("thread_id"),
            }
        return web.json_response(
            {
                "successful": True,
                "data": data,
                "error": None,
            }
        )


class FakeSlackServer:
    """Lightweight aiohttp server mimicking the Slack Web API."""

    def __init__(self, test_user_email: str, store: MockPlatformMessageStore):
        self._test_user_email = test_user_email
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self.chat_post_blocks_error: str | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/api/users.info", self._users_info)
        app.router.add_route(
            "*", "/api/conversations.history", self._conversations_history
        )
        app.router.add_route(
            "*", "/api/conversations.replies", self._conversations_replies
        )
        app.router.add_route("*", "/api/conversations.list", self._conversations_list)
        app.router.add_route("*", "/api/chat.postMessage", self._chat_post_message)
        app.router.add_route("*", "/api/chat.update", self._chat_update)
        app.router.add_route("*", "/api/chat.delete", self._chat_delete)
        app.router.add_route("*", "/api/chat.startStream", self._chat_start_stream)
        app.router.add_route("*", "/api/chat.appendStream", self._chat_append_stream)
        app.router.add_route("*", "/api/chat.stopStream", self._chat_stop_stream)
        app.router.add_route("*", "/api/reactions.add", self._reactions_add)
        app.router.add_route("*", "/api/files.info", self._files_info)
        app.router.add_route(
            "*", "/api/assistant.threads.setStatus", self._assistant_threads_set_status
        )
        app.router.add_route(
            "*", "/api/assistant.threads.setTitle", self._assistant_threads_set_title
        )
        app.router.add_route(
            "*",
            "/api/assistant.threads.setSuggestedPrompts",
            self._assistant_threads_set_suggested_prompts,
        )
        app.router.add_route("*", "/api/views.open", self._views_open)
        app.router.add_route("*", "/api/views.publish", self._views_publish)
        app.router.add_route("*", "/api/conversations.info", self._conversations_info)
        app.router.add_route(
            "*", "/api/files.getUploadURLExternal", self._files_get_upload_url_external
        )
        app.router.add_route(
            "*",
            "/api/files.completeUploadExternal",
            self._files_complete_upload_external,
        )
        app.router.add_post("/upload/{file_id}", self._upload_raw_file)
        app.router.add_get("/files/{file_id}", self._download_file)

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

    async def _collect_params(self, request: web.Request) -> dict[str, Any]:
        payload: dict[str, Any] = dict(request.query)
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
        payload.update(_request_contract(request))
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
        params = await self._collect_params(request)
        self._store.add("SLACK_HISTORY", params)
        return web.json_response(
            {
                "ok": True,
                "messages": [
                    {
                        "ts": "1700000000.777002",
                        "user": "U-CURRENT",
                        "text": "Current channel message",
                    },
                    {
                        "ts": "1700000000.777001",
                        "user": "U-CONTEXT",
                        "text": "Earlier support context",
                        "user_profile": {"display_name": "Earlier Teammate"},
                        "files": [
                            {
                                "id": "F-CONTEXT",
                                "name": "support-context.txt",
                                "mimetype": "text/plain",
                                "filetype": "txt",
                                "size": 128,
                                "url_private_download": (
                                    f"{self.base_url}/files/F-CONTEXT/download"
                                ),
                            }
                        ],
                    },
                ],
                "response_metadata": {"next_cursor": ""},
            }
        )

    async def _conversations_replies(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_REPLIES", params)
        return web.json_response(
            {
                "ok": True,
                "messages": [
                    {
                        "ts": "1700000000.777000",
                        "user": "U-CONTEXT",
                        "text": "Thread root context",
                    },
                    {
                        "ts": "1700000000.777001",
                        "user": "U-SECOND",
                        "text": "Thread follow-up context",
                    },
                    {
                        "ts": "1700000000.777002",
                        "user": "U-CURRENT",
                        "text": "Current channel message",
                    },
                ],
            }
        )

    async def _conversations_list(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_CHANNEL_LIST", params)
        return web.json_response(
            {
                "ok": True,
                "channels": [
                    {"id": "C-SUPPORT", "name": "support", "is_member": True},
                    {"id": "C-INCIDENTS", "name": "incidents", "is_member": False},
                    {"name": "malformed-without-id"},
                ],
                "response_metadata": {"next_cursor": ""},
            }
        )

    async def _chat_post_message(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        if self.chat_post_blocks_error is not None and params.get("blocks"):
            error = self.chat_post_blocks_error
            self.chat_post_blocks_error = None
            self._store.add("SLACK_FAILED", {"error": error, **params})
            return web.json_response({"ok": False, "error": error})
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

    async def _chat_start_stream(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_STREAM_START", params)
        ts = f"1700000001.{len(self._store.get_all('SLACK_STREAM_START')):06d}"
        return web.json_response(
            {"ok": True, "ts": ts, "channel": params.get("channel")}
        )

    async def _chat_append_stream(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_STREAM_APPEND", params)
        return web.json_response(
            {"ok": True, "ts": params.get("ts"), "channel": params.get("channel")}
        )

    async def _chat_stop_stream(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_STREAM_STOP", params)
        return web.json_response(
            {"ok": True, "ts": params.get("ts"), "channel": params.get("channel")}
        )

    async def _reactions_add(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_REACTIONS", params)
        return web.json_response({"ok": True})

    async def _files_info(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        file_id = str(params.get("file") or "F-SURFACE-E2E")
        return web.json_response(
            {
                "ok": True,
                "file": {
                    "id": file_id,
                    "name": "slack-customer-brief.txt",
                    "mimetype": "text/plain",
                    "url_private_download": (
                        f"http://127.0.0.1:{self._port}/files/{file_id}"
                    ),
                },
            }
        )

    async def _download_file(self, request: web.Request) -> web.Response:
        file_id = request.match_info["file_id"]
        self._store.add(
            "SLACK_FILE_DOWNLOAD",
            {"file_id": file_id, **_request_contract(request)},
        )
        return web.Response(
            body=f"fake Slack attachment {file_id}".encode(),
            content_type="text/plain",
        )

    async def _assistant_threads_set_status(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_STATUS", params)
        return web.json_response({"ok": True})

    async def _assistant_threads_set_title(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_THREAD_TITLE", params)
        return web.json_response({"ok": True})

    async def _assistant_threads_set_suggested_prompts(
        self, request: web.Request
    ) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_SUGGESTED_PROMPTS", params)
        return web.json_response({"ok": True})

    async def _views_open(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_VIEWS_OPEN", params)
        view_id = f"V{len(self._store.get_all('SLACK_VIEWS_OPEN')):09d}"
        return web.json_response(
            {"ok": True, "view": self._view_envelope(params.get("view"), view_id)}
        )

    async def _views_publish(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_VIEWS_PUBLISH", params)
        view_id = f"V{len(self._store.get_all('SLACK_VIEWS_PUBLISH')):09d}"
        return web.json_response(
            {"ok": True, "view": self._view_envelope(params.get("view"), view_id)}
        )

    def _view_envelope(self, raw_view: Any, view_id: str) -> dict[str, Any]:
        return {
            **self._parse_view(raw_view),
            "id": view_id,
            "team_id": "T-SURFACE-E2E",
            "hash": f"{view_id}.{int(time.time())}",
        }

    @staticmethod
    def _parse_view(raw: Any) -> dict[str, Any]:
        """Recover the ``view`` dict submitted to ``views.open``/``views.publish``.

        slack_sdk posts ``view`` as JSON, but ``_collect_params`` stringifies
        every field uniformly (as ``chat.appendStream``'s ``chunks`` already
        does), so what arrives here is a dict's ``repr()``, not JSON — hence
        ``ast.literal_eval`` rather than ``json.loads``.
        """
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        with suppress(ValueError, SyntaxError):
            parsed = ast.literal_eval(str(raw))
            if isinstance(parsed, dict):
                return parsed
        return {}

    async def _conversations_info(self, request: web.Request) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_CHANNEL_INFO", params)
        channel_id = str(params.get("channel") or "C-SURFACE-E2E")
        return web.json_response(
            {
                "ok": True,
                "channel": {
                    "id": channel_id,
                    "name": "support",
                    "is_channel": True,
                    "is_group": False,
                    "is_im": False,
                    "is_private": False,
                    "is_archived": False,
                    "is_member": True,
                },
            }
        )

    async def _files_get_upload_url_external(
        self, request: web.Request
    ) -> web.Response:
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
                "size": len(uploaded.file.read())
                if hasattr(uploaded, "file")
                else None,
            },
        )
        return web.Response(text="OK")

    async def _files_complete_upload_external(
        self, request: web.Request
    ) -> web.Response:
        params = await self._collect_params(request)
        self._store.add("SLACK_FILE_COMPLETE", params)
        return web.json_response({"ok": True, "files": []})


# Azure AD's real ``invalid_grant``/``unauthorized_client`` shapes for the two
# client_credentials failures client.py's ``_get_token`` classifies by code
# (see the ``AADSTS65001``/``AADSTS700016`` substring checks there). Keyed by
# AADSTS code so ``queue_oauth_error`` can select either without the caller
# needing to know the surrounding envelope.
_AAD_ERROR_RESPONSES: dict[str, tuple[str, str]] = {
    "AADSTS65001": (
        "invalid_grant",
        "AADSTS65001: The user or administrator has not consented to use the "
        "application with ID 'fake-teams-app-id'. Send an interactive "
        "authorization request for this user and resource.",
    ),
    "AADSTS700016": (
        "unauthorized_client",
        "AADSTS700016: Application with identifier 'fake-teams-app-id' was "
        "not found in the directory 'fake-teams-tenant'. This can happen if "
        "the application has not been installed by the administrator of the "
        "tenant.",
    ),
}


class FakeTeamsServer:
    """Lightweight aiohttp server mimicking the MS Teams Bot Framework."""

    def __init__(self, test_user_email: str, store: MockPlatformMessageStore):
        self._test_user_email = test_user_email
        self._store = store
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self.graph_failure_status: int | None = None
        self._oauth_error_code: str | None = None
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
        app.router.add_get(
            "/teams/v3/attachments/{attachment_id}/views/original",
            self._get_attachment,
        )
        app.router.add_get(
            "/graph/v1.0/teams/{team_id}/channels/{channel_id}/messages",
            self._get_channel_messages,
        )
        app.router.add_get(
            "/graph/v1.0/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
            self._get_channel_messages,
        )
        app.router.add_post(
            "/oauth/{tenant}/oauth2/v2.0/token",
            self._oauth_token,
        )
        app.router.add_get(
            "/graph/v1.0/shares/{token}/driveItem",
            self._graph_shares_drive_item,
        )
        app.router.add_get(
            "/graph/v1.0/drives/{drive_id}/items/{item_id}/content",
            self._graph_drive_item_content,
        )
        # Registered before the host:path wildcard below so the literal
        # "/sites/root" special case (site_path == "/" in
        # ``_resolve_sharepoint_site_id``) and the "drive/root:...:/content"
        # shape (the content URL ``_resolve_sharepoint_file_content_url``
        # constructs and later fetches) both win over the generic matcher.
        app.router.add_get("/graph/v1.0/sites/root", self._graph_sites_root)
        app.router.add_get(
            "/graph/v1.0/sites/{site_id}/drive/root:{item_path:.*}:/content",
            self._graph_sharepoint_root_content,
        )
        app.router.add_get(
            "/graph/v1.0/sites/{spec:.+}",
            self._graph_sites_by_spec,
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
    def service_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/teams"

    @property
    def graph_base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/graph/v1.0"

    def attachment_url(self, attachment_id: str) -> str:
        return f"{self.service_url}/v3/attachments/{attachment_id}/views/original"

    @property
    def openid_config_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/botframework/.well-known/openidconfiguration"

    @property
    def oauth_base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/oauth"

    def queue_oauth_error(self, code: str) -> None:
        """Make the NEXT OAuth token request fail with the given AADSTS error.

        Mirrors real Azure AD's 400 ``invalid_grant``/``unauthorized_client``
        response shape so ``client.py``'s ``_get_token`` classification logic
        (the ``AADSTS65001``/``AADSTS700016`` substring checks) actually
        fires. Unrecognized codes still fail the next request, with a generic
        description carrying the code.
        """
        self._oauth_error_code = code

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
        self._store.add(
            "TEAMS",
            {"path": str(request.rel_url), "body": body, **_request_contract(request)},
        )
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
                **_request_contract(request),
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

    async def _get_attachment(self, request: web.Request) -> web.Response:
        attachment_id = request.match_info["attachment_id"]
        self._store.add(
            "TEAMS_ATTACHMENT",
            {"attachment_id": attachment_id, **_request_contract(request)},
        )
        return web.Response(
            body=f"fake Teams attachment {attachment_id}".encode(),
            content_type="text/plain",
        )

    async def _get_channel_messages(self, request: web.Request) -> web.Response:
        self._store.add(
            "TEAMS_GRAPH",
            {
                "team_id": request.match_info["team_id"],
                "channel_id": request.match_info["channel_id"],
                "message_id": request.match_info.get("message_id"),
                **_request_contract(request),
            },
        )
        if self.graph_failure_status is not None:
            status = self.graph_failure_status
            self.graph_failure_status = None
            return web.json_response(
                {"error": {"code": "ScriptedGraphFailure"}},
                status=status,
            )
        earlier_reply = {
            "id": "teams-context-001",
            "body": {
                "contentType": "html",
                "content": "<p>Earlier customer context</p>",
            },
            "from": {
                "user": {
                    "id": "teams-context-user",
                    "displayName": "Earlier Participant",
                }
            },
            "attachments": [
                {
                    "name": "customer-context.pdf",
                    "contentType": "application/pdf",
                    "contentUrl": self.attachment_url("customer-context"),
                }
            ],
        }
        values = [earlier_reply]
        # Microsoft Graph's thread-replies endpoint returns replies only; the
        # root message is returned by the channel messages endpoint. Mirroring
        # that distinction prevents an impossible duplicate "current message"
        # from entering production's thread-context normalization path.
        if request.match_info.get("message_id") is None:
            values.append(
                {
                    "id": "1776236638028",
                    "body": {
                        "contentType": "text",
                        "content": "Current message",
                    },
                    "from": {
                        "user": {
                            "id": "b20e77ef-bd6b-4636-9f5b-20dd28beba24",
                            "displayName": "Surface Test User",
                        }
                    },
                    "attachments": [],
                }
            )
        return web.json_response({"value": values})

    async def _oauth_token(self, request: web.Request) -> web.Response:
        tenant = request.match_info["tenant"]
        form = await request.post()
        self._store.add(
            "TEAMS_OAUTH_TOKEN",
            {
                "tenant": tenant,
                "grant_type": form.get("grant_type"),
                "client_id": form.get("client_id"),
                "scope": form.get("scope"),
                **_request_contract(request),
            },
        )
        if self._oauth_error_code is not None:
            code = self._oauth_error_code
            self._oauth_error_code = None
            error, description = _AAD_ERROR_RESPONSES.get(
                code, ("invalid_grant", f"{code}: Scripted OAuth failure")
            )
            return web.json_response(
                {"error": error, "error_description": description},
                status=400,
            )
        return web.json_response(
            {
                "token_type": "Bearer",
                "expires_in": 3600,
                "ext_expires_in": 3600,
                "access_token": f"fake-teams-token-{tenant}",
            }
        )

    async def _graph_shares_drive_item(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        self._store.add(
            "TEAMS_GRAPH_SHARES",
            {"token": token, **_request_contract(request)},
        )
        item_id = f"shared-item-{len(self._store.get_all('TEAMS_GRAPH_SHARES'))}"
        return web.json_response(
            {
                "id": item_id,
                "name": "shared-file.txt",
                "parentReference": {"driveId": "drive-e2e-1"},
            }
        )

    async def _graph_drive_item_content(self, request: web.Request) -> web.Response:
        drive_id = request.match_info["drive_id"]
        item_id = request.match_info["item_id"]
        self._store.add(
            "TEAMS_GRAPH_CONTENT",
            {"drive_id": drive_id, "item_id": item_id, **_request_contract(request)},
        )
        return web.Response(
            body=f"fake SharePoint content {drive_id}/{item_id}".encode(),
            content_type="text/plain",
        )

    async def _graph_sites_root(self, request: web.Request) -> web.Response:
        self._store.add(
            "TEAMS_GRAPH_SITES", {"path": "root", **_request_contract(request)}
        )
        return web.json_response({"id": "site-root-e2e", "displayName": "Root Site"})

    async def _graph_sites_by_spec(self, request: web.Request) -> web.Response:
        # ``_resolve_sharepoint_site_id`` builds this as
        # "{hostname}:{site_path}" (e.g. "contoso.sharepoint.com:/sites/eng").
        spec = request.match_info["spec"]
        hostname, _, site_path = spec.partition(":")
        self._store.add(
            "TEAMS_GRAPH_SITES",
            {
                "hostname": hostname,
                "site_path": site_path,
                **_request_contract(request),
            },
        )
        return web.json_response(
            {"id": f"site-{hostname}", "displayName": site_path or hostname}
        )

    async def _graph_sharepoint_root_content(
        self, request: web.Request
    ) -> web.Response:
        site_id = request.match_info["site_id"]
        item_path = request.match_info["item_path"]
        self._store.add(
            "TEAMS_GRAPH_CONTENT",
            {
                "site_id": site_id,
                "item_path": item_path,
                **_request_contract(request),
            },
        )
        return web.Response(
            body=f"fake SharePoint content {site_id}:{item_path}".encode(),
            content_type="text/plain",
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
        app.router.add_get("/v21.0/{media_id}", self._get_media_info)
        app.router.add_get("/media/{media_id}", self._download_media)

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
                **_request_contract(request),
            },
        )
        return web.json_response({"id": media_id})

    async def _get_media_info(self, request: web.Request) -> web.Response:
        media_id = request.match_info["media_id"]
        self._store.add(
            "WHATSAPP_MEDIA_INFO",
            {"media_id": media_id, **_request_contract(request)},
        )
        return web.json_response(
            {
                "id": media_id,
                "mime_type": "text/plain",
                "url": f"{self.api_base}/media/{media_id}",
            }
        )

    async def _download_media(self, request: web.Request) -> web.Response:
        media_id = request.match_info["media_id"]
        self._store.add(
            "WHATSAPP_MEDIA_DOWNLOAD",
            {"media_id": media_id, **_request_contract(request)},
        )
        return web.Response(
            body=f"fake WhatsApp media {media_id}".encode(),
            content_type="text/plain",
        )

    async def _send_message(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add("WHATSAPP", {**body, **_request_contract(request)})
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
        self._updates: list[dict[str, Any]] = []

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
        app.router.add_post("/bot{token}/getUpdates", self._get_updates)
        app.router.add_post("/bot{token}/setMyCommands", self._set_my_commands)
        app.router.add_post("/bot{token}/setChatMenuButton", self._set_chat_menu_button)
        app.router.add_post("/bot{token}/setMyDescription", self._set_my_description)
        app.router.add_post(
            "/bot{token}/setMyShortDescription", self._set_my_short_description
        )
        app.router.add_post("/bot{token}/setMyProfilePhoto", self._set_my_profile_photo)
        app.router.add_post(
            "/bot{token}/getManagedBotToken", self._get_managed_bot_token
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

    def queue_update(self, payload: dict[str, Any]) -> None:
        """Make one deterministic update available to the polling receiver."""
        self._updates.append(payload)

    async def _get_updates(self, request: web.Request) -> web.Response:
        form = await request.post()
        offset = int(str(form.get("offset") or "0"))
        ready = [
            update
            for update in self._updates
            if int(update.get("update_id") or 0) >= offset
        ]
        if ready:
            delivered_ids = {int(update.get("update_id") or 0) for update in ready}
            self._updates = [
                update
                for update in self._updates
                if int(update.get("update_id") or 0) not in delivered_ids
            ]
        else:
            # Avoid a hot loop while retaining a fast deterministic poll.
            await asyncio.sleep(0.05)
        self._store.add(
            "TELEGRAM_GET_UPDATES",
            {"offset": offset, "count": len(ready), **_request_contract(request)},
        )
        return web.json_response({"ok": True, "result": ready})

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
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: message is too long",
                },
                status=400,
            )
        self._store.add("TELEGRAM", {**body, **_request_contract(request)})
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
        self._store.add("TELEGRAM_EDIT", {**body, **_request_contract(request)})
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
                **_request_contract(request),
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
                **_request_contract(request),
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
                **_request_contract(request),
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _delete_webhook(self, request: web.Request) -> web.Response:
        failure = self._maybe_fail("deleteWebhook")
        if failure is not None:
            return failure
        # Telegram accepts form-encoded Bot API requests. Native polling uses
        # that production contract, while webhook setup uses JSON, so the fake
        # must support both instead of raising JSONDecodeError on valid form
        # data.
        if request.content_type == "application/json":
            body = await request.json()
        else:
            body = dict(await request.post())
        self._registered_webhook = None
        self._store.add(
            "TELEGRAM_WEBHOOK",
            {
                "method": "deleteWebhook",
                "token": request.match_info["token"],
                "body": body,
                **_request_contract(request),
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

    async def _set_my_commands(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "TELEGRAM_CONFIGURATION",
            {
                "method": "setMyCommands",
                "token": request.match_info["token"],
                "body": body,
                **_request_contract(request),
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _set_chat_menu_button(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "TELEGRAM_CONFIGURATION",
            {
                "method": "setChatMenuButton",
                "token": request.match_info["token"],
                "body": body,
                **_request_contract(request),
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _set_my_description(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "TELEGRAM_CONFIGURATION",
            {
                "method": "setMyDescription",
                "token": request.match_info["token"],
                "body": body,
                **_request_contract(request),
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _set_my_short_description(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "TELEGRAM_CONFIGURATION",
            {
                "method": "setMyShortDescription",
                "token": request.match_info["token"],
                "body": body,
                **_request_contract(request),
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _set_my_profile_photo(self, request: web.Request) -> web.Response:
        # setMyProfilePhoto is a multipart upload: an "attach://" reference
        # field (JSON-encoded) plus the file part it points at — same shape as
        # sendVoice/sendDocument above.
        form = await request.post()
        photo_field = form.get("profile_photo")
        self._store.add(
            "TELEGRAM_CONFIGURATION",
            {
                "method": "setMyProfilePhoto",
                "token": request.match_info["token"],
                "photo": str(form.get("photo")) if form.get("photo") else None,
                "has_file": photo_field is not None,
                "filename": getattr(photo_field, "filename", None),
                **_request_contract(request),
            },
        )
        return web.json_response({"ok": True, "result": True})

    async def _get_managed_bot_token(self, request: web.Request) -> web.Response:
        # Telegram's "create a bot on behalf of a user" API, used by the
        # managed-bot self-service flow (telegram_manager_updates.py's
        # ``_provision``). Called with the *manager* bot's own token; the
        # child bot's token is minted server-side and handed back as
        # ``result``.
        body = await request.json()
        user_id = body.get("user_id")
        self._store.add(
            "TELEGRAM_MANAGED_BOT_TOKEN",
            {
                "token": request.match_info["token"],
                "user_id": user_id,
                **_request_contract(request),
            },
        )
        return web.json_response(
            {"ok": True, "result": f"{user_id}:FAKE-MANAGED-BOT-TOKEN-e2e"}
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
        self._store.add("GMAIL", {**body, **_request_contract(request)})
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
        self._store.add("RESEND", {**body, **_request_contract(request)})
        return web.json_response(
            {"id": f"resend-message-{len(self._store.get_all('RESEND'))}"}
        )


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
            {
                "message_id": message_id,
                "query": dict(request.query),
                **_request_contract(request),
            },
        )
        return web.json_response(payload)

    async def _send_mail(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add("OUTLOOK", {**body, **_request_contract(request)})
        return web.Response(status=202)

    async def _reply(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._store.add(
            "OUTLOOK_REPLY",
            {
                "message_id": request.match_info["message_id"],
                "body": body,
                **_request_contract(request),
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
                **_request_contract(request),
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
                **_request_contract(request),
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
                **_request_contract(request),
            },
        )
        return web.json_response(
            {"id": f"attachment-{len(self._store.get_all('OUTLOOK_DRAFT_ATTACHMENT'))}"}
        )

    async def _send_draft(self, request: web.Request) -> web.Response:
        self._store.add(
            "OUTLOOK_DRAFT_SEND",
            {
                "message_id": request.match_info["message_id"],
                **_request_contract(request),
            },
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


def build_resend_svix_headers(
    *,
    raw_body: bytes,
    signing_secret: str,
    timestamp: int | None = None,
    svix_id: str = "msg_e2e_resend",
) -> dict[str, str]:
    """Build a valid Svix (Resend inbound) signature header set for ``raw_body``."""
    ts = str(timestamp or int(time.time()))
    secret = signing_secret
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_") :]
    key = base64.b64decode(secret)
    signed = svix_id.encode() + b"." + ts.encode() + b"." + raw_body
    signature = base64.b64encode(
        hmac.new(key, signed, hashlib.sha256).digest()
    ).decode()
    return {
        "svix-id": svix_id,
        "svix-timestamp": ts,
        "svix-signature": f"v1,{signature}",
        "Content-Type": "application/json",
    }


def slack_delivered(store: MockPlatformMessageStore) -> list[str]:
    """Every user-visible string Slack was asked to show, in arrival order.

    Slack carries a reply three ways now, and all three moved under these
    tests at least once: ``chat.postMessage`` text, the markdown ``blocks`` of
    that same message (the body lives there, and ``text`` is only the
    notification preview), and ``chat.appendStream`` chunks when the answer
    closes the live stream opened at run start. Reading any single one of them
    reports "nothing was delivered" for a reply the user can see perfectly
    well. One entry per message, so counting entries counts replies rather
    than the fields a reply happens to occupy.
    """
    return [text for _, text in _slack_deliveries(store)]


def _slack_deliveries(store: MockPlatformMessageStore) -> list[tuple[dict, str]]:
    """One entry per Slack *message*: the call that opened it, and its text.

    A streamed answer is not one API call. ``TokenStreamMixin`` flushes the
    model's deltas every 280 characters or 0.8 seconds, so "Thanks, recorded."
    can reach Slack as ``chat.appendStream`` carrying "Thanks, rec" and then
    "orded." against the same stream ``ts``. On screen that is a single
    message, so it is a single entry here — otherwise every assertion that
    counts replies over-counts one answer as several, and every assertion
    looking for a phrase misses it at whichever seam the flush happened to
    fall on.
    """
    deliveries: list[tuple[dict, list[str]]] = []
    streams: dict[tuple[str, str], list[str]] = {}
    for platform, message in store.get_ordered("SLACK", "SLACK_STREAM_APPEND"):
        text = _slack_reply_text(platform, message)
        if not text:
            continue
        if platform == "SLACK":
            deliveries.append((message, [text]))
            continue
        # Keyed on the stream, not on arrival order: two surfaces can stream
        # into the same thread at once, and their appends interleave.
        key = (str(message.get("channel") or ""), str(message.get("ts") or ""))
        appended = streams.get(key)
        if appended is not None:
            appended.append(text)
            continue
        streams[key] = parts = [text]
        deliveries.append((message, parts))
    return [(message, "".join(parts)) for message, parts in deliveries]


def _slack_reply_text(platform: str, message: dict) -> str:
    if platform != "SLACK":
        return _slack_stream_text(message)
    parts = [message.get("text"), message.get("blocks")]
    return "\n".join(str(part) for part in parts if part)


def _slack_stream_text(message: dict) -> str:
    """The visible text of one ``chat.appendStream`` call.

    ``markdown_text`` chunks carry model text and are concatenated as written:
    a chunk is a fragment of a sentence, not a line, so joining them with a
    separator would insert whitespace the user never sees. Every other chunk
    (the step timeline's ``task_update``) keeps its repr on its own line, which
    is the form the assertions about steps read.
    """
    chunks = _slack_stream_chunks(message)
    if not chunks:
        raw = message.get("chunks")
        return str(raw) if raw else ""
    rendered = ""
    for chunk in chunks:
        if chunk.get("type") == "markdown_text":
            rendered += str(chunk.get("text") or "")
        else:
            rendered += f"\n{chunk}" if rendered else str(chunk)
    return rendered


def _slack_stream_chunks(message: dict) -> list[dict]:
    """The chunk list of a ``chat.appendStream`` call, decoded.

    ``_collect_params`` records every parameter through ``str()``, so ``chunks``
    arrives as the repr of the list slack_sdk sent rather than the list itself.
    Parsing it back is confined to here; a shape that will not parse falls back
    to being treated as opaque text by the caller.
    """
    raw = message.get("chunks")
    if not raw:
        return []
    if isinstance(raw, list):
        return [chunk for chunk in raw if isinstance(chunk, dict)]
    try:
        decoded = ast.literal_eval(str(raw))
    except ValueError, SyntaxError:
        return []
    if not isinstance(decoded, list):
        return []
    return [chunk for chunk in decoded if isinstance(chunk, dict)]


def slack_delivered_calls(store: MockPlatformMessageStore) -> list[dict]:
    """The raw Slack calls that carried a reply, in arrival order.

    ``slack_delivered`` is the text; this is for the handful of assertions
    that need the call itself — which channel it went to, or how many replies
    there were. Both posted messages and stream appends carry ``channel``. One
    entry per message, so a streamed answer is the call that opened its stream.
    """
    return [message for message, _ in _slack_deliveries(store)]


async def wait_for_slack_replies(
    store: MockPlatformMessageStore,
    min_count: int = 1,
    timeout_seconds: float = 30.0,
) -> list[dict]:
    """Wait until at least ``min_count`` replies reach Slack by any transport."""

    async def probe() -> list[dict]:
        return slack_delivered_calls(store)

    return await eventually(
        label=f"{min_count}+ Slack repl{'y' if min_count == 1 else 'ies'}",
        probe=probe,
        done=lambda calls: len(calls) >= min_count,
        timeout_seconds=timeout_seconds,
        interval_seconds=0.15,
    )


async def wait_for_slack_text(
    store: MockPlatformMessageStore,
    needle: str,
    timeout_seconds: float = 30.0,
) -> list[str]:
    """Wait until ``needle`` reaches Slack by any transport; return all of it.

    Waiting on one bucket is what made these tests flaky-looking: the prompt
    lands as a posted message immediately, so a wait on posted messages
    returns straight away and the assertion runs before the streamed answer
    has arrived.
    """

    async def probe() -> list[str]:
        return slack_delivered(store)

    return await eventually(
        label=f"Slack text containing {needle!r}",
        probe=probe,
        done=lambda delivered: any(needle in text for text in delivered),
        timeout_seconds=timeout_seconds,
        interval_seconds=0.15,
    )


async def wait_for_messages(
    store: MockPlatformMessageStore,
    platform: str,
    min_count: int = 1,
    timeout_seconds: float = 30.0,
    predicate: Callable[[dict], bool] | None = None,
) -> list[dict]:
    # `probe` always returns the FULL, unfiltered bucket for `platform` --
    # `done` applies `predicate` only to decide readiness, never to shrink
    # the returned value. Several of the ~80 call sites re-filter the
    # returned list by a *different* predicate than the one passed here
    # (e.g. test_teams_surface_e2e.py does its own comprehension over the
    # result), so returning `matching` instead of the raw list would
    # silently break them.
    async def probe() -> list[dict]:
        return store.get_all(platform)

    def done(messages: list[dict]) -> bool:
        matching = messages if predicate is None else list(filter(predicate, messages))
        return len(matching) >= min_count

    return await eventually(
        label=f"{min_count}+ {platform} message(s)",
        probe=probe,
        done=done,
        timeout_seconds=timeout_seconds,
        interval_seconds=0.15,
    )
