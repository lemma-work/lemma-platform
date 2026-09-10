"""Unit tests for the package-free OpenAPI HTTP executor (httpx MockTransport)."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.modules.connectors.infrastructure.adapters.openapi_http_executor import (
    OpenApiHttpExecutionError,
    OpenApiHttpExecutor,
)

CREDS = {"access_token": "tok123", "token_type": "Bearer"}


def _executor(handler) -> OpenApiHttpExecutor:
    """Executor driving an injected MockTransport client.

    The client is injected rather than built per call, so tests hand one in the
    same way the composition layer hands in the process-shared pool.
    """
    return OpenApiHttpExecutor(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def _run(monkeypatch, execution, payload, handler):
    return await _executor(handler).execute(
        connector_id="github",
        operation_name="op",
        execution=execution,
        payload=payload,
        third_party_credentials=CREDS,
    )


@pytest.mark.asyncio
async def test_get_path_substitution_and_array_query(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={"ok": True})

    hr = {
        "mode": "openapi",
        "method": "GET",
        "path": "/repos/{owner}/{repo}/issues",
        "server_url": "https://api.github.com",
        "path_params": ["owner", "repo"],
        "query_params": [{"name": "labels", "style": "form", "explode": False}],
        "header_params": [],
        "request_body": None,
        "response": {"binary": False},
        "default_headers": {"User-Agent": "lemma"},
    }
    result = await _run(
        monkeypatch,
        hr,
        {"owner": "me", "repo": "demo", "labels": ["bug", "p1"]},
        handler,
    )
    assert result == {"ok": True}
    assert seen["url"] == "https://api.github.com/repos/me/demo/issues?labels=bug%2Cp1"
    assert seen["auth"] == "Bearer tok123"
    assert seen["ua"] == "lemma"


@pytest.mark.asyncio
async def test_post_json_body(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = request.content
        return httpx.Response(201, json={"number": 7})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/repos/{owner}/{repo}/issues",
        "server_url": "https://api.github.com",
        "path_params": ["owner", "repo"],
        "query_params": [],
        "header_params": [],
        "request_body": {
            "content_type": "application/json",
            "field": "body",
            "binary_fields": [],
            "form_fields": [],
        },
        "response": {"binary": False},
    }
    result = await _run(
        monkeypatch,
        hr,
        {"owner": "me", "repo": "demo", "body": {"title": "hi"}},
        handler,
    )
    assert result == {"number": 7}
    assert seen["method"] == "POST"
    assert "application/json" in seen["content_type"]
    assert b'"title"' in seen["body"]


@pytest.mark.asyncio
async def test_multipart_upload_from_base64(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        seen["content"] = request.content
        return httpx.Response(201, json={"uploaded": True})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/upload",
        "server_url": "https://api.example.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": {
            "content_type": "multipart/form-data",
            "field": "body",
            "binary_fields": ["file"],
            "form_fields": ["name"],
        },
        "response": {"binary": False},
    }
    payload = {
        "body": {
            "file": {"base64": base64.b64encode(b"hello-bytes").decode()},
            "name": "a.txt",
        }
    }
    result = await _run(monkeypatch, hr, payload, handler)
    assert result == {"uploaded": True}
    assert seen["content_type"].startswith("multipart/form-data")
    assert b"hello-bytes" in seen["content"]
    assert b"a.txt" in seen["content"]


@pytest.mark.asyncio
async def test_octet_stream_single_body(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        seen["content"] = request.content
        return httpx.Response(201, json={"ok": True})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/assets",
        "server_url": "https://uploads.example.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": {
            "content_type": "application/octet-stream",
            "field": "body",
            "binary_fields": ["body"],
            "form_fields": [],
        },
        "response": {"binary": False},
    }
    payload = {"body": {"base64": base64.b64encode(b"\x00\x01\x02rawblob").decode()}}
    await _run(monkeypatch, hr, payload, handler)
    assert seen["content_type"] == "application/octet-stream"
    assert seen["content"] == b"\x00\x01\x02rawblob"


@pytest.mark.asyncio
async def test_binary_response_returns_binary_content_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"tarball-bytes",
            headers={
                "content-type": "application/gzip",
                "content-disposition": 'attachment; filename="out.tgz"',
            },
        )

    hr = {
        "mode": "openapi",
        "method": "GET",
        "path": "/tarball",
        "server_url": "https://api.github.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": None,
        "response": {"binary": True},
    }
    result = await _run(monkeypatch, hr, {}, handler)
    assert result.type == "binary_content"
    assert base64.b64decode(result.content_base64) == b"tarball-bytes"
    assert result.file_name == "out.tgz"


@pytest.mark.asyncio
async def test_missing_path_param_raises(monkeypatch):
    hr = {
        "mode": "openapi",
        "method": "GET",
        "path": "/repos/{owner}",
        "server_url": "https://api.github.com",
        "path_params": ["owner"],
        "query_params": [],
        "header_params": [],
        "request_body": None,
        "response": {"binary": False},
    }
    with pytest.raises(OpenApiHttpExecutionError, match="path parameter"):
        await _run(monkeypatch, hr, {}, lambda r: httpx.Response(200))


_GIT_REF_EXECUTION = {
    "mode": "openapi",
    "method": "PATCH",
    "path": "/repos/{owner}/{repo}/git/refs/{ref}",
    "server_url": "https://api.github.com",
    "path_params": ["owner", "repo", "ref"],
    "multi_segment_path_params": ["ref"],
    "query_params": [],
    "header_params": [],
    "request_body": None,
    "response": {"binary": False},
}


@pytest.mark.asyncio
async def test_a_multi_segment_path_param_keeps_its_slashes(monkeypatch):
    """A git ref is several segments in one placeholder.

    Percent-encoding its slash is what a path parameter normally deserves, and
    is exactly what makes GitHub answer 404 for every real ref.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    await _run(
        monkeypatch,
        _GIT_REF_EXECUTION,
        {"owner": "me", "repo": "demo", "ref": "heads/main"},
        handler,
    )
    assert seen["url"] == "https://api.github.com/repos/me/demo/git/refs/heads/main"


@pytest.mark.asyncio
async def test_a_multi_segment_path_param_cannot_climb_out_of_its_endpoint(
    monkeypatch,
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the request must never be made")

    with pytest.raises(OpenApiHttpExecutionError, match="relative segments"):
        await _run(
            monkeypatch,
            _GIT_REF_EXECUTION,
            {"owner": "me", "repo": "demo", "ref": "../../../user/repos"},
            handler,
        )


@pytest.mark.asyncio
async def test_an_ordinary_path_param_still_escapes_its_slashes(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    execution = {**_GIT_REF_EXECUTION}
    del execution["multi_segment_path_params"]
    await _run(
        monkeypatch,
        execution,
        {"owner": "me", "repo": "demo", "ref": "heads/main"},
        handler,
    )
    assert seen["url"].endswith("/git/refs/heads%2Fmain")


@pytest.mark.asyncio
async def test_non_2xx_raises_with_status(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation failed"})

    hr = {
        "mode": "openapi",
        "method": "POST",
        "path": "/x",
        "server_url": "https://api.github.com",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": None,
        "response": {"binary": False},
    }
    with pytest.raises(OpenApiHttpExecutionError) as exc:
        await _run(monkeypatch, hr, {}, handler)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_raw_mode_and_ssrf_guard(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"path": str(request.url.path)})

    hr = {"mode": "raw", "server_url": "https://api.github.com"}
    result = await _run(
        monkeypatch, hr, {"method": "GET", "path": "/repos/o/r"}, handler
    )
    assert result == {"path": "/repos/o/r"}

    with pytest.raises(OpenApiHttpExecutionError, match="absolute path"):
        await _run(
            monkeypatch, hr, {"method": "GET", "path": "https://evil.com/x"}, handler
        )

    with pytest.raises(OpenApiHttpExecutionError, match="absolute path"):
        await _run(monkeypatch, hr, {"method": "GET", "path": "//evil.com/x"}, handler)


class TestRedirectsAreGuarded:
    """A redirect is a fresh target, and the tenant chooses where it points.

    For an `http` install the org admin supplies `server_url`. If the client
    followed redirects itself, that host could answer `302 ->
    169.254.169.254` and walk straight past the guard, which only ever saw the
    original URL. These pin that every hop is re-validated, and that the
    account's credentials do not travel to another origin.
    """

    @pytest.mark.asyncio
    async def test_a_redirect_to_the_metadata_service_is_refused(self, monkeypatch):
        from app.core.config import settings

        # Even with the self-hosting hatch open, link-local stays refused.
        monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if "169.254" in str(request.url):
                return httpx.Response(200, json={"secret": "instance-credentials"})
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = OpenApiHttpExecutor(client)
        with pytest.raises(OpenApiHttpExecutionError) as caught:
            await executor.execute(
                connector_id="c",
                operation_name="op",
                execution={"mode": "openapi", "method": "GET", "path": "/x"},
                payload={},
                third_party_credentials={"api_key": "k"},
                connection_config={"server_url": "http://api.tenant.test"},
            )
        assert caught.value.details.get("reason") == "link_local_address"
        # The metadata service was never contacted.
        assert not any("169.254" in url for url in seen)

    @pytest.mark.asyncio
    async def test_credentials_do_not_follow_a_redirect_to_another_origin(
        self, monkeypatch
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
        received: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(request.headers)
            if request.url.host == "elsewhere.test":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(302, headers={"location": "http://elsewhere.test/x"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = OpenApiHttpExecutor(client)
        await executor.execute(
            connector_id="c",
            operation_name="op",
            execution={
                "mode": "openapi",
                "method": "GET",
                "path": "/x",
                "auth": {"type": "header", "name": "X-Api-Key"},
            },
            payload={},
            third_party_credentials={"api_key": "super-secret"},
            connection_config={"server_url": "http://api.tenant.test"},
        )
        assert len(received) == 2
        # The tenant's key reached their own host, and nobody else's.
        assert any("super-secret" in str(v) for v in received[0].values())
        assert not any("super-secret" in str(v) for v in received[1].values())

    @pytest.mark.asyncio
    async def test_a_redirect_loop_is_cut_off(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
        hops = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal hops
            hops += 1
            return httpx.Response(302, headers={"location": "http://api.tenant.test/x"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = OpenApiHttpExecutor(client)
        with pytest.raises(OpenApiHttpExecutionError) as caught:
            await executor.execute(
                connector_id="c",
                operation_name="op",
                execution={"mode": "openapi", "method": "GET", "path": "/x"},
                payload={},
                third_party_credentials={},
                connection_config={"server_url": "http://api.tenant.test"},
            )
        assert caught.value.details.get("reason") == "too_many_redirects"
        assert hops <= 5

    @pytest.mark.asyncio
    async def test_raw_passthrough_follows_no_redirect_at_all(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
        hops = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal hops
            hops += 1
            return httpx.Response(302, headers={"location": "http://elsewhere.test/x"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = OpenApiHttpExecutor(client)
        # Raw hands the redirect back rather than following it, so the other
        # origin is never contacted -- one request, and no second hop.
        await executor.execute(
            connector_id="c",
            operation_name="op",
            execution={"mode": "raw"},
            payload={"method": "GET", "path": "/x"},
            third_party_credentials={},
            connection_config={"server_url": "http://api.tenant.test"},
        )
        assert hops == 1


# --- form-encoded bodies, response envelopes, path-parameter defaults --------
#
# Everything below exists because Slack and Gmail are not shaped like GitHub.
# Slack's Web API is form-encoded RPC that answers every call with HTTP 200 and
# puts success in the body; Gmail marks `userId` required on all 79 of its
# operations and the only value anyone passes is "me".


def _form_execution(**overrides):
    execution = {
        "kind": "http",
        "mode": "openapi",
        "method": "POST",
        "path": "/chat.postMessage",
        "server_url": "https://slack.com/api",
        "path_params": [],
        "query_params": [],
        "header_params": [],
        "request_body": {
            "content_type": "application/x-www-form-urlencoded",
            "field": "body",
            "binary_fields": [],
            "form_fields": ["channel", "text", "link_names"],
        },
        "response": {"binary": False},
    }
    execution.update(overrides)
    return execution


_SLACK_ENVELOPE = {
    "success_field": "ok",
    "error_field": "error",
    "status_by_error": {
        "invalid_auth": 401,
        "missing_scope": 403,
        "channel_not_found": 404,
        "ratelimited": 429,
    },
    "default_status": 400,
}


@pytest.mark.asyncio
async def test_a_form_body_is_sent_as_form_fields_not_as_a_file(monkeypatch):
    """The regression that made every Slack POST unusable.

    Without a form-urlencoded branch the body collapsed to a single blob, the
    descriptor named it a binary field, and the executor sent raw bytes.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True, "ts": "1.0"})

    result = await _run(
        monkeypatch,
        _form_execution(),
        {"body": {"channel": "C1", "text": "hi", "link_names": True}},
        handler,
    )

    assert seen["content_type"] == "application/x-www-form-urlencoded"
    assert sorted(seen["body"].split("&")) == [
        "channel=C1",
        "link_names=true",
        "text=hi",
    ]
    assert result == {"ok": True, "ts": "1.0"}


@pytest.mark.asyncio
async def test_a_form_field_left_out_is_not_sent_as_an_empty_value(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True})

    await _run(
        monkeypatch,
        _form_execution(),
        {"body": {"channel": "C1", "text": None}},
        handler,
    )

    assert seen["body"] == "channel=C1"


@pytest.mark.asyncio
async def test_a_200_that_says_ok_false_fails_with_the_mapped_status(monkeypatch):
    """Slack reports failure in a 200 body; without this it read as success."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    with pytest.raises(OpenApiHttpExecutionError) as excinfo:
        await _run(
            monkeypatch,
            _form_execution(response={"binary": False, "envelope": _SLACK_ENVELOPE}),
            {"body": {"channel": "nope"}},
            handler,
        )

    assert excinfo.value.status_code == 404
    assert excinfo.value.details["error"] == "channel_not_found"
    assert "channel_not_found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_unmapped_envelope_error_falls_back_to_the_default_status(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "something_new"})

    with pytest.raises(OpenApiHttpExecutionError) as excinfo:
        await _run(
            monkeypatch,
            _form_execution(response={"binary": False, "envelope": _SLACK_ENVELOPE}),
            {"body": {}},
            handler,
        )

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_an_envelope_success_is_returned_untouched(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "channels": [{"id": "C1"}]})

    result = await _run(
        monkeypatch,
        _form_execution(response={"binary": False, "envelope": _SLACK_ENVELOPE}),
        {"body": {}},
        handler,
    )

    assert result == {"ok": True, "channels": [{"id": "C1"}]}


@pytest.mark.asyncio
async def test_without_an_envelope_a_200_saying_ok_false_is_still_a_result(monkeypatch):
    """The envelope is opt-in: a connector that never declares one is unchanged."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    result = await _run(monkeypatch, _form_execution(), {"body": {}}, handler)

    assert result == {"ok": False, "error": "channel_not_found"}


@pytest.mark.asyncio
async def test_a_path_parameter_falls_back_to_its_declared_default(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"emailAddress": "a@b.c"})

    await _run(
        monkeypatch,
        {
            "kind": "http",
            "mode": "openapi",
            "method": "GET",
            "path": "/gmail/v1/users/{userId}/profile",
            "server_url": "https://gmail.googleapis.com",
            "path_params": ["userId"],
            "path_param_defaults": {"userId": "me"},
            "query_params": [],
            "header_params": [],
            "request_body": None,
            "response": {"binary": False},
        },
        {},
        handler,
    )

    assert seen["url"] == "https://gmail.googleapis.com/gmail/v1/users/me/profile"


@pytest.mark.asyncio
async def test_a_supplied_path_parameter_beats_its_default(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    await _run(
        monkeypatch,
        {
            "kind": "http",
            "mode": "openapi",
            "method": "GET",
            "path": "/gmail/v1/users/{userId}/profile",
            "server_url": "https://gmail.googleapis.com",
            "path_params": ["userId"],
            "path_param_defaults": {"userId": "me"},
            "query_params": [],
            "header_params": [],
            "request_body": None,
            "response": {"binary": False},
        },
        {"userId": "someone@example.com"},
        handler,
    )

    assert seen["url"].endswith("/users/someone%40example.com/profile")
