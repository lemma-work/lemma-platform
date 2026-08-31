"""Real execution, real sandbox: the behaviours this work exists to guarantee.

These do not mock the workspace. A container is started, commands actually run
in it, and the assertions are about what the agent observes — because every bug
this suite covers was a case of the *contract* being wrong while the mechanism
worked, which a mocked test cannot catch.

Covers:
  * a command that outlives its call keeps running and can be polled to
    completion, with its real exit code
  * a failing build reports the real stderr and does not fail the run
  * a process past its deadline is reaped instead of pinning the sandbox
  * `web_fetch` captures a real page into the workspace
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from aiohttp import web
from fastapi import status

import app.modules.workspace.services.workspace_tool_runtime as workspace_runtime
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    WriteStdinRequest,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
    list_processes_internal,
    write_stdin_internal,
)
from app.modules.test_support.e2e.waiters import eventually

# asyncio, not anyio, like the other 139 e2e files. Neither of these two files
# uses anyio for anything -- no anyio API, no trio, no task groups -- but the
# marker made pytest-anyio run each test in its own fresh event loop while every
# fixture around them runs in the session loop pytest.ini configures. That is
# invisible until something is held across the boundary: a pooled Postgres
# connection opened during fixture setup and reused in the test body dies with
# "got Future attached to a different loop".


@dataclass
class _FetchablePage:
    """One page of HTML on loopback, so the good URL in the batch is local."""

    url: str = ""
    _runner: "web.AppRunner | None" = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._page)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets if site._server else []  # noqa: SLF001
        assert sockets
        self.url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}/"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _page(self, request: web.Request) -> web.Response:
        del request
        # Comfortably over `_THIN_CONTENT_CHARS` (120). Below it, extraction is
        # read as degenerate and the fetch is retried through the browser --
        # which is a real behaviour worth keeping, and would make the
        # `fetched_with == "http"` assertion below silently untrue. The
        # in-tree comment notes example.com extracts to 167 clean characters;
        # this is deliberately in the same range.
        return web.Response(
            text=(
                "<html><body><h1>A fetchable page</h1>"
                "<p>This page exists so the plain HTTP fetch path has something "
                "local to read. It carries enough ordinary prose to clear the "
                "thin-content floor, so extraction succeeds on the first pass "
                "and no browser render is spent re-reading a page that was "
                "already read correctly.</p></body></html>"
            ),
            content_type="text/html",
        )


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

# Long enough that it cannot finish inside one default wait window, short enough
# that the suite stays usable.
_SLOW_COMMAND_SECONDS = 45


async def _agent_context(authenticated_client, fixed_test_org, fixed_test_user):
    await workspace_runtime.close_workspace_tool_runtimes()
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Long Running Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod = response.json()
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="long_running_e2e",
    )

    # Provision the container before the tests that assert on timing. Starting
    # one cold takes longer than a short `timeout_seconds`, so an unwarmed first
    # call reports SandboxUnavailable — correct behaviour (it is retryable and
    # says so), but it is not what these tests are measuring.
    #
    # Retried because that is exactly what the tool contract tells its caller to
    # do. Running after the rest of the suite, the first attempt can also hit a
    # pooled connection to a sandbox that has since been released
    # (RemoteProtocolError), which is the same retryable class.
    async def _attempt_warmup():
        return await exec_command_internal(
            ctx,
            ExecCommandRequest(
                comment="warm the sandbox", cmd="true", timeout_seconds=180
            ),
        )

    await eventually(
        label="sandbox warmup",
        probe=_attempt_warmup,
        done=lambda warmup: warmup.success,
        timeout_seconds=30.0,
        interval_seconds=2.0,
    )
    return ctx


async def _poll_until_complete(ctx, process_id: str, *, budget_seconds: int):
    """Drive the loop the tool documentation tells the agent to use."""
    deadline = time.monotonic() + budget_seconds
    collected: list[str] = []
    while time.monotonic() < deadline:
        result = await write_stdin_internal(
            ctx,
            WriteStdinRequest(
                process_id=process_id,
                chars="",
                yield_time_ms=5000,
                comment="poll for completion",
            ),
        )
        collected.append(result.stdout or "")
        if result.completed:
            return result, "".join(collected)
    raise AssertionError(
        f"process {process_id} did not finish within {budget_seconds}s"
    )


async def test_a_command_that_outlives_its_call_can_be_polled_to_completion(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """The core long-build guarantee, against a real container.

    The tool used to tell the model `timeout_seconds` "always returns
    `completed: true`", so an agent whose build outlived the wait window
    believed it had been cancelled and started a second one.
    """
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="a build that outlives its wait window",
            cmd=(
                f"echo BUILD_STARTED; sleep {_SLOW_COMMAND_SECONDS}; "
                "echo BUILD_FINISHED; exit 0"
            ),
            timeout_seconds=10,
        ),
    )

    # It did not finish, and it was NOT cancelled.
    assert started.success, started.error
    assert started.completed is False
    assert started.process_id, "a still-running command must hand back a handle"
    assert "BUILD_STARTED" in (started.stdout or "")

    final, output = await _poll_until_complete(
        ctx, started.process_id, budget_seconds=_SLOW_COMMAND_SECONDS + 60
    )

    assert final.completed is True
    assert final.exit_code == 0, f"exit={final.exit_code} output={output!r}"
    assert "BUILD_FINISHED" in output
    # Polling must not restart anything: the start banner is printed once.
    assert (started.stdout or "").count("BUILD_STARTED") == 1


async def test_a_failing_build_reports_its_real_error_and_exit_code(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """A build that fails is a tool result, not a run failure — the agent has to
    see the compiler's own message to fix it."""
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    result = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="a build that fails",
            cmd=(
                "mkdir -p broken && "
                "printf 'def f(:\\n' > broken/bad.py && "
                "python3 -m py_compile broken/bad.py"
            ),
            timeout_seconds=30,
        ),
    )

    assert result.completed is True
    assert result.exit_code not in (0, None), result
    combined = f"{result.stdout or ''}{result.stderr or ''}"
    assert "SyntaxError" in combined or "invalid syntax" in combined, combined


async def test_a_long_process_survives_across_separate_tool_calls(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """Each poll is a separate tool call resolving the process by id.

    The process->session binding used to expire after 30 minutes while the
    process was still running, after which polls were silently routed to the
    default shell and the agent was told its process had vanished.
    """
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="incremental output",
            cmd="for i in 1 2 3 4 5; do echo TICK_$i; sleep 3; done; echo ALL_DONE",
            timeout_seconds=10,
        ),
    )
    assert started.completed is False
    process_id = started.process_id
    assert process_id

    listed = await list_processes_internal(ctx, None)
    assert any(item.process_id == process_id for item in listed.processes), (
        f"the running process should be discoverable: {listed}"
    )

    final, output = await _poll_until_complete(ctx, process_id, budget_seconds=90)
    assert final.completed is True
    assert "ALL_DONE" in output
    # Every tick was captured across the separate polls; nothing was lost.
    combined = (started.stdout or "") + output
    for tick in range(1, 6):
        assert f"TICK_{tick}" in combined, combined


async def test_an_expired_process_is_reaped_instead_of_pinning_the_sandbox(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    monkeypatch,
):
    """A forgotten `npm run dev` used to run forever, and the idle sweeper — which
    refuses to release a sandbox with live processes — kept the sandbox with it."""
    del configure_workspace_api_url
    from app.modules.workspace.config import workspace_settings

    # A two-second lifetime so the sweep has something to find.
    monkeypatch.setattr(workspace_settings, "process_max_lifetime_seconds", 2)
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="a process nobody will ever wait for",
            cmd="sleep 600",
            timeout_seconds=10,
        ),
    )
    assert started.completed is False
    process_id = started.process_id
    assert process_id

    # The in-sandbox reaper sweeps on its own interval; drive it directly so the
    # test asserts the behaviour rather than the schedule.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = await write_stdin_internal(
            ctx,
            WriteStdinRequest(process_id=process_id, chars="", yield_time_ms=2000),
        )
        if result.completed:
            assert result.error or result.exit_code is not None
            return
    raise AssertionError("the past-deadline process was never reaped")


async def test_web_fetch_captures_a_real_page_into_the_workspace(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """`web_fetch` against a live URL, writing into the real sandbox filesystem.

    This is the one path that has to be exercised for real: the whole feature is
    a shell pipeline inside the container.
    """
    del configure_workspace_api_url
    from app.modules.agent.tools.web.models import WebFetchRequest
    from app.modules.agent.tools.web.web_fetch import web_fetch_internal

    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    result = await web_fetch_internal(
        ctx,
        WebFetchRequest(
            urls=["https://example.com/"],
            formats=["markdown"],
            out_dir="research",
            comment="capture a page for the e2e",
        ),
    )

    assert result.success, result
    page = result.pages[0]
    assert page.success, page.error
    assert page.fetched_with == "http", "a static page should skip the browser"
    assert page.files["markdown"].startswith("research/")
    assert page.preview and "Example Domain" in page.preview

    # The file really is on disk in the workspace, and holds the article text.
    listing = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="confirm the capture landed",
            cmd=f"cat {page.files['markdown']}",
            timeout_seconds=30,
        ),
    )
    assert listing.exit_code == 0, listing
    assert "Example Domain" in (listing.stdout or "")


async def test_a_research_batch_saves_pages_larger_than_a_shell_can_carry(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    monkeypatch,
):
    """A long article must land on disk, in a real container.

    The document used to be embedded in the `sh -c` argument of a heredoc, and
    Linux caps a single argument at MAX_ARG_STRLEN (128KB) regardless of
    ARG_MAX. A Wikipedia article extracts to ~185KB of markdown, so exactly the
    pages worth capturing failed with E2BIG — and `web_fetch` read that as "this
    page needs a browser", spending a Chrome render per page and turning a
    five-source research call into minutes of apparent hang.

    The extraction is stubbed so the test does not depend on a third party
    staying large; everything past it — the file API, the container filesystem,
    the size on disk — is real.
    """
    del configure_workspace_api_url
    from app.modules.agent.tools.web import web_fetch as web_fetch_module
    from app.modules.agent.tools.web.models import WebFetchRequest
    from app.modules.agent.tools.web.page_extract import ExtractedPage
    from app.modules.agent.tools.web.web_fetch import web_fetch_internal

    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    body = "Ashwin took 537 Test wickets across 106 matches. " * 4000  # ~195KB
    urls = [f"https://en.wikipedia.org/wiki/Subject_{index}" for index in range(3)]

    async def fake_fetch(url: str) -> ExtractedPage:
        return ExtractedPage(
            url=url,
            title="Ravichandran Ashwin",
            markdown=body,
            content_type="text/html",
        )

    monkeypatch.setattr(web_fetch_module, "fetch_and_clean", fake_fetch)

    result = await web_fetch_internal(
        ctx,
        WebFetchRequest(urls=urls, out_dir="research", comment="a large batch"),
    )

    assert result.success, result
    assert len(result.pages) == 3
    for page in result.pages:
        assert page.success, page.error
        assert page.fetched_with == "http", "a fetched page must not need Chrome"
        assert page.characters and page.characters > 128 * 1024

    # On disk in the container, at full size — the response stayed small.
    sizes = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="confirm the large captures landed whole",
            cmd="wc -c research/*.md",
            timeout_seconds=30,
        ),
    )
    assert sizes.exit_code == 0, sizes
    counts = [
        int(line.split()[0])
        for line in (sizes.stdout or "").splitlines()
        if line.split() and line.split()[0].isdigit() and "total" not in line
    ]
    assert len(counts) == 3, sizes.stdout
    assert all(count > 128 * 1024 for count in counts), counts
    assert len(result.model_dump_json()) < 8000, "the response must stay bounded"


async def test_web_fetch_forces_the_browser_and_captures_a_rendered_page(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """`render=True` forces the browser path, exercised against a real page.

    The http path (above) is the default and is fast; `render` exists for
    pages that only exist after JavaScript runs, or when the caller wants a
    PDF/screenshot. `_capture_with_browser`/`_finish`'s success branch, the
    real `save-webpage` script, and the real Agent Browser session inside the
    sandbox all run for real here — nothing about the browser path was
    exercised by any other test, which only drove the cheap http path.
    """
    del configure_workspace_api_url
    from app.modules.agent.tools.web.models import WebFetchRequest
    from app.modules.agent.tools.web.web_fetch import web_fetch_internal

    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    result = await web_fetch_internal(
        ctx,
        WebFetchRequest(
            urls=["https://example.com/"],
            render=True,
            out_dir="research",
            comment="force the browser render path",
        ),
    )

    assert result.success, result
    page = result.pages[0]
    assert page.success, page.error
    assert page.fetched_with == "browser", "render=True must skip the http path"
    assert page.files["markdown"].startswith("research/")
    assert page.preview and "Example Domain" in page.preview
    assert page.title

    # The file really is on disk in the workspace, rendered by the browser.
    listing = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="confirm the rendered capture landed",
            cmd=f"cat {page.files['markdown']}",
            timeout_seconds=30,
        ),
    )
    assert listing.exit_code == 0, listing
    assert "Example Domain" in (listing.stdout or "")


@pytest.mark.fast_workspace
@pytest.mark.timeout(300)
async def test_web_fetch_rejects_malformed_and_unsafe_urls_before_fetching(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    monkeypatch,
):
    """`_validate` and `assert_safe_url` run for every URL before either fetch
    path starts, and a bad URL in the batch must not sink the good ones.

    The good URL is served from this process. It used to be
    `https://example.com/`, which put a live internet fetch inside a *required*
    check — `@pytest.mark.fast_workspace` suppresses the `workspace` marker, so
    this runs in the merge-gating `agent` shard, and `timeout(300)` meant a
    slow DNS answer burned five minutes of it.

    Reaching a loopback address needs the self-hosting hatch, and turning it on
    makes the test say more rather than less: `_is_disallowed_address` keeps
    link-local denied even with `allow_private` set, precisely because
    169.254.169.254 is the cloud metadata service and never a fetch target. So
    the metadata assertion below is now also a statement that opening the hatch
    for your own subnet does not open it for your instance credentials.
    """
    del configure_workspace_api_url
    from app.core.config import settings
    from app.modules.agent.tools.web.models import WebFetchRequest
    from app.modules.agent.tools.web.web_fetch import web_fetch_internal

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)

    served = _FetchablePage()
    await served.start()
    try:
        ctx = await _agent_context(
            authenticated_client, fixed_test_org, fixed_test_user
        )

        result = await web_fetch_internal(
            ctx,
            WebFetchRequest(
                urls=[
                    "ftp://example.com/file",
                    "https://",
                    "http://169.254.169.254/latest/meta-data",
                    served.url,
                ],
                comment="mixed malformed, unsafe, and valid URLs",
            ),
        )
    finally:
        await served.stop()

    assert result.success, "one good URL among the bad ones must still succeed"
    by_url = {page.url: page for page in result.pages}
    assert "Only http(s) URLs" in (by_url["ftp://example.com/file"].error or "")
    assert "no host" in (by_url["https://"].error or "")
    assert "not a permitted fetch target" in (
        by_url["http://169.254.169.254/latest/meta-data"].error or ""
    ), "the metadata service must stay refused even with private targets allowed"
    assert by_url[served.url].success
    assert by_url[served.url].fetched_with == "http"


@pytest.mark.fast_workspace
@pytest.mark.timeout(300)
async def test_web_fetch_reports_a_clear_error_when_the_workspace_is_unreachable(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    monkeypatch,
):
    """A sandbox that cannot be reached must not look like "no pages found".

    Exercises the `except Exception` branch around acquiring the workspace
    session: everything below it in `_capture_batch` is already proven by the
    tests above, so only the acquisition failure needs injecting here.
    """
    del configure_workspace_api_url
    from app.modules.agent.tools.web import web_fetch as web_fetch_module
    from app.modules.agent.tools.web.models import WebFetchRequest
    from app.modules.agent.tools.web.web_fetch import web_fetch_internal

    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    async def fake_get_session(*_args, **_kwargs):
        raise RuntimeError("sandbox connection refused")

    monkeypatch.setattr(web_fetch_module, "get_workspace_session", fake_get_session)

    result = await web_fetch_internal(
        ctx,
        WebFetchRequest(urls=["https://example.com/"], comment="workspace is down"),
    )

    assert result.success is False
    assert result.pages[0].success is False
    assert "Could not reach the workspace" in (result.pages[0].error or "")
    assert "Retry if the pages are still needed" in (result.pages[0].error or "")
    assert result.message == "No pages could be captured."


async def _pdf_in_pod(authenticated_client, fixed_test_org, fixed_test_user):
    """Upload a real multi-line PDF and return (ctx, pod_id, path)."""
    from app.modules.datastore.tests.e2e.harness import DatastoreApi, build_pdf_bytes

    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)
    api = DatastoreApi(authenticated_client, str(ctx.pod_id))
    pdf = build_pdf_bytes(
        "Ingestion Pipeline Overview",
        "Stage 1: Ingest -> Stage 2: Validate -> Stage 3: Store",
        "Throughput table: fast=1200rps slow=40rps",
    )
    uploaded = await api.upload_file(
        "pipeline.pdf",
        pdf,
        directory_path="/",
        content_type="application/pdf",
    )
    return ctx, uploaded["path"]


async def test_pdf_pages_render_to_real_images_for_a_vision_model(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """DIRECT mode: the model receives the rendered page bytes inline.

    Real pypdfium2 rasterisation against a real uploaded PDF — this is the path
    an agent uses to *see* a diagram or a table that the markdown extraction
    flattens away.
    """
    del configure_workspace_api_url
    from app.modules.agent.domain.vision import AgentVisionMode
    from app.modules.agent.tools.pod.models import ViewDocumentPagesRequest
    from app.modules.agent.tools.pod.pydantic_adapter import pod_view_document_pages
    from pydantic_ai import BinaryContent, ToolReturn

    ctx, path = await _pdf_in_pod(authenticated_client, fixed_test_org, fixed_test_user)
    ctx.vision_mode = AgentVisionMode.DIRECT

    result = await pod_view_document_pages(
        SimpleNamespace(deps=ctx),
        ViewDocumentPagesRequest(path=path, page_start=1, instructions="read it"),
    )

    assert isinstance(result, ToolReturn), result
    images = [c for c in result.content if isinstance(c, BinaryContent)]
    assert images, "a vision model must receive the rendered page"
    # A real JPEG, not a placeholder.
    assert images[0].media_type == "image/jpeg"
    assert images[0].data.startswith(b"\xff\xd8"), "not JPEG magic bytes"
    assert len(images[0].data) > 2000, "suspiciously small render"
    assert result.return_value["success"] is True


async def test_pdf_pages_reach_a_text_only_model_as_words(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
    monkeypatch,
):
    """DELEGATED mode: the same real render, described instead of embedded.

    This is the bug that shipped: `pod_view_document_pages` handed
    `BinaryContent` to whatever model was running, so a text-only model got
    image content in its history and the provider rejected the request.
    """
    del configure_workspace_api_url
    from app.modules.agent.domain.vision import AgentVisionMode
    from app.modules.agent.tools import vision_delegation
    from app.modules.agent.tools.pod import pydantic_adapter as pod_adapter
    from app.modules.agent.tools.pod.models import ViewDocumentPagesRequest
    from pydantic_ai import ToolReturn

    ctx, path = await _pdf_in_pod(authenticated_client, fixed_test_org, fixed_test_user)
    ctx.vision_mode = AgentVisionMode.DELEGATED

    seen: dict[str, object] = {}

    async def fake_describe(images, *, instructions, organization_id, user_id):
        # The delegate receives the genuinely rendered bytes.
        seen["jpeg_prefixes"] = [image.data[:2] for image in images]
        seen["labels"] = [image.label for image in images]
        return "A three-stage pipeline: Ingest -> Validate -> Store."

    # Patched on `vision_delegation`, which is where the delegation actually
    # lives: it was extracted out of the pod adapter to keep that file under the
    # size ratchet, and this patch kept naming the old home. It has been raising
    # AttributeError here ever since — e2e is not a required check, so a red
    # test shipped.
    monkeypatch.setattr(vision_delegation, "describe_images", fake_describe)

    result = await pod_adapter.pod_view_document_pages(
        SimpleNamespace(deps=ctx),
        ViewDocumentPagesRequest(
            path=path, page_start=1, instructions="describe the pipeline"
        ),
    )

    assert not isinstance(result, ToolReturn), (
        "a ToolReturn carries BinaryContent, which is what breaks a text-only model"
    )
    assert result["success"] is True
    assert result["viewed_by"] == "vision_model"
    assert "pipeline" in result["descriptions"][0]["description"].lower()
    assert seen["jpeg_prefixes"] == [b"\xff\xd8"], "delegate got real JPEG bytes"
    assert path in str(seen["labels"][0])


async def test_view_image_direct_mode_returns_binary_content_for_a_real_workspace_file(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """DIRECT mode: `view_image` hands the model the rendered bytes inline.

    `test_scripted_todo_and_workspace_tools_stream_and_persist_real_results`
    (hermetic journeys) already drives `view_image` end to end, but that run's
    mock runtime profile carries no vision capability, so it only ever
    exercises the DELEGATED/unavailable branch. This is the DIRECT branch:
    the same shape of coverage `test_pdf_pages_render_to_real_images_for_a_
    vision_model` proves for `pod_view_document_pages`, but for a workspace
    file read straight off the real sandbox disk.
    """
    del configure_workspace_api_url
    from app.modules.agent.domain.vision import AgentVisionMode
    from app.modules.agent.tools.workspace_cli.models import ViewImageRequest
    from app.modules.agent.tools.workspace_cli.workspace_cli import (
        view_image_internal,
    )
    from pydantic_ai import BinaryContent, ToolReturn

    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)
    ctx.vision_mode = AgentVisionMode.DIRECT

    written = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="create a real image on the sandbox disk",
            cmd=(
                'python3 -c "import base64,pathlib; '
                "pathlib.Path('pixel.png').write_bytes(base64.b64decode("
                "'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z2S8AAAAASUVORK5CYII='))\""
            ),
            timeout_seconds=30,
        ),
    )
    assert written.exit_code == 0, written

    result = await view_image_internal(
        ctx, ViewImageRequest(workspace_file_path="pixel.png")
    )

    assert isinstance(result, ToolReturn), result
    images = [c for c in result.content if isinstance(c, BinaryContent)]
    assert images, "a DIRECT-mode vision model must receive the rendered bytes"
    assert images[0].media_type == "image/png"
    assert result.return_value.success is True
    assert result.return_value.source == "workspace"
    assert result.return_value.file_path == "pixel.png"


async def test_compaction_bounds_a_history_built_from_real_tool_output(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """A long coding session must stay inside the context window.

    Production runs Fireworks (OpenAI-compatible), which has no provider-native
    compaction, so this is entirely ours. The output here is genuinely produced
    by a container — build-log-shaped text, which is exactly the content the old
    `len(text)/4` estimate under-counted by ~27%, letting a "100k" history reach
    the provider at 140k and be rejected.
    """
    del configure_workspace_api_url
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from app.modules.agent.domain.value_objects import HarnessOptions
    from app.modules.agent.infrastructure.harnesses.history import (
        build_history_processors,
    )
    from app.modules.agent.services.history_tokens import count_model_message_tokens

    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    # Real output from a real container, shaped like an npm install log.
    produced = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="generate build-log-shaped output",
            cmd=(
                "for i in $(seq 1 400); do "
                'echo "npm WARN deprecated pkg-$i@1.0.$i: no longer maintained"; '
                "done"
            ),
            timeout_seconds=60,
        ),
    )
    assert produced.completed is True, produced
    log = produced.stdout or ""
    assert len(log) > 10_000, f"expected a large real log, got {len(log)} chars"

    # A coding session: many tool rounds each carrying that output.
    history: list[object] = [
        ModelRequest(parts=[UserPromptPart(content="Build the project.")])
    ]
    for index in range(30):
        call_id = f"call-{index}"
        history.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="exec_command",
                        args={"cmd": "npm install"},
                        tool_call_id=call_id,
                    )
                ]
            )
        )
        history.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="exec_command",
                        content=log,
                        tool_call_id=call_id,
                    )
                ]
            )
        )

    raw_size = count_model_message_tokens(history)
    assert raw_size > 110_000, (
        f"history not large enough to exercise the guard: {raw_size}"
    )

    ceiling = 40_000
    processors = build_history_processors(
        HarnessOptions(
            model_name="glm-4.6",
            # Isolate the deterministic guard: the LLM summarizer is covered by
            # unit tests and would need a live model here.
            history_summarization_enabled=False,
            history_hard_token_ceiling=ceiling,
        ),
        summarization_model=None,
    )
    guard = processors[-1]

    compacted = await guard(history)

    assert count_model_message_tokens(compacted) <= ceiling
    assert compacted[-1] is history[-1], "the most recent turn must survive"

    # And it is still a well-formed history: no tool result without its call.
    seen_calls: set[str] = set()
    for message in compacted:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                seen_calls.add(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                assert part.tool_call_id in seen_calls, (
                    "a tool result was kept without its call — the provider "
                    "rejects that, trading a context error for a validation one"
                )
