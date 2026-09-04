from __future__ import annotations

import re
import shlex

from uuid import uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from app.modules.agent.tools.browser import browser, script
from app.modules.agent.tools.browser import pydantic_adapter
from app.modules.agent.tools.browser.models import (
    BrowserActRequest,
    BrowserReadRequest,
    BrowserResult,
    BrowserScreenshotRequest,
)


def _sentinel_in(script: str) -> str:
    """The delimiter this call generated, read back out of its own script."""
    found = re.search(r"__LEMMA_BROWSER_SPLIT_[0-9a-f]{32}__", script)
    assert found is not None, script
    return found.group(0)


def _run_context() -> RunContext:
    from app.modules.agent.tools.context import BaseAgentContext

    return RunContext(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )


def test_cli_quotes_every_interpolated_value():
    hostile = "https://x.test/?a=1&b=2; rm -rf /"
    rendered = script.cli(["open", hostile])
    # What matters is what the shell parses it into: one command, one argument,
    # with the embedded `;` still inside it rather than starting a second one.
    assert shlex.split(rendered) == ["agent-browser", "open", hostile]


def test_build_script_reports_state_even_when_the_action_fails():
    sentinel = "SENT"
    rendered = script.build_script(
        [script.cli(["click", "@e1"])], sentinel=sentinel, snapshot_argv=None
    )
    # The action is one group whose status is captured, and the reporting tail is
    # joined with `;` so it survives a failing click.
    assert "__lemma_rc=$?" in rendered
    assert rendered.count(" ; ") >= 3
    assert "agent-browser get url" in rendered
    assert "agent-browser get title" in rendered


def test_build_script_chains_actions_with_and():
    rendered = script.build_script(
        [
            script.cli(["open", "https://x.test"]),
            script.cli(["wait", "--url", "**/d"]),
        ],
        sentinel="SENT",
        snapshot_argv=script.snapshot_argv_for(interactive_only=True),
    )
    assert "agent-browser open https://x.test && agent-browser wait" in rendered
    assert "agent-browser snapshot --json -i" in rendered


def test_parse_script_output_splits_sections():
    sentinel = "SENT"
    stdout = "\n".join(
        [
            "opened ok",
            sentinel,
            "rc=0",
            sentinel,
            "https://x.test/a",
            sentinel,
            "A page",
            sentinel,
            '{"refs":[]}',
        ]
    )
    parsed = script.parse_script_output(stdout, sentinel=sentinel)
    assert parsed.action == "opened ok"
    assert parsed.return_code == 0
    assert parsed.url == "https://x.test/a"
    assert parsed.title == "A page"
    assert parsed.snapshot == '{"refs":[]}'


def test_parse_script_output_tolerates_a_script_that_died_early():
    parsed = script.parse_script_output("boom", sentinel="SENT")
    assert parsed.action == "boom"
    assert parsed.return_code is None
    assert parsed.url is None
    assert parsed.snapshot is None


@pytest.mark.parametrize("code", [124, 137])
def test_a_shed_browser_is_reported_as_shed(code: int):
    advice = script.classify_browser_failure(return_code=code, output="")
    assert advice == script.BROWSER_SHED_ADVICE


def test_a_closed_target_is_reported_as_shed():
    advice = script.classify_browser_failure(
        return_code=1, output="Error: Target page, context or browser has been closed"
    )
    assert advice == script.BROWSER_SHED_ADVICE


def test_an_ordinary_page_error_is_not_reported_as_shed():
    assert (
        script.classify_browser_failure(
            return_code=1, output="No element matches selector @e9"
        )
        is None
    )


def test_head_truncate_keeps_the_top_of_a_snapshot():
    text = "a" * 100
    trimmed, truncated = script.head_truncate(text, max_tokens=10)
    assert truncated is True
    assert trimmed is not None and trimmed.startswith("a" * 40)
    untouched, flag = script.head_truncate("short", max_tokens=10)
    assert (untouched, flag) == ("short", False)


def test_every_sentinel_is_unique():
    assert script.new_sentinel() != script.new_sentinel()


@pytest.mark.parametrize(
    "request_kwargs,expected_fragment",
    [
        ({"action": "click"}, "`target` is required"),
        ({"action": "fill", "target": "@e1"}, "`text` is required"),
        ({"action": "press"}, "`key` is required"),
    ],
)
def test_a_bad_action_is_returned_not_raised(request_kwargs, expected_fragment):
    problem = script.act_steps(BrowserActRequest(**request_kwargs))
    assert isinstance(problem, str)
    assert expected_fragment in problem


def test_act_steps_build_the_expected_argv():
    assert script.act_steps(BrowserActRequest(action="click", target="@e3")) == [
        ["click", "@e3"]
    ]
    assert script.act_steps(
        BrowserActRequest(action="fill", target="@e3", text="a@b.test")
    ) == [["fill", "@e3", "a@b.test"]]
    assert script.act_steps(BrowserActRequest(action="press", key="Enter")) == [
        ["press", "Enter"]
    ]
    assert script.act_steps(
        BrowserActRequest(action="scroll", scroll_direction="down", scroll_amount=250)
    ) == [["scroll", "down", "250"]]


def test_the_network_read_names_an_action():
    """Bare `agent-browser network` is a usage error, not a log."""
    assert script.read_steps(BrowserReadRequest(what="network")) == [
        ["network", "requests"]
    ]


def test_read_steps_require_what_they_need():
    assert isinstance(script.read_steps(BrowserReadRequest(what="text")), str)
    assert isinstance(
        script.read_steps(BrowserReadRequest(what="attr", target="@e1")), str
    )
    assert script.read_steps(BrowserReadRequest(what="url")) == [["get", "url"]]
    assert script.read_steps(BrowserReadRequest(what="html")) == [
        ["get", "html", "html"]
    ]
    assert script.read_steps(
        BrowserReadRequest(what="attr", target="@e1", attribute="href")
    ) == [["get", "attr", "@e1", "href"]]


def test_screenshot_argv_picks_a_lossless_format_only_when_annotating():
    plain = script.screenshot_argv(BrowserScreenshotRequest(), path="/tmp/a.jpeg")
    assert "--screenshot-format" in plain and plain[-1] == "/tmp/a.jpeg"
    annotated = script.screenshot_argv(
        BrowserScreenshotRequest(annotate=True), path="/tmp/a.png"
    )
    assert "--annotate" in annotated and "--screenshot-format" not in annotated
    assert "--full" in script.screenshot_argv(
        BrowserScreenshotRequest(full_page=True), path="/tmp/a.jpeg"
    )


@pytest.mark.asyncio
async def test_act_returns_the_page_after_the_action():
    """The refs in a result must describe the page as it is *after* the act."""
    captured: dict[str, str] = {}

    async def fake_run(ctx, script, operation):
        captured["script"] = script
        sentinel = _sentinel_in(script)
        return (
            "\n".join(
                [
                    "",
                    sentinel,
                    "rc=0",
                    sentinel,
                    "https://x.test/dashboard",
                    sentinel,
                    "Dashboard",
                    sentinel,
                    "@e1 button Sign out",
                ]
            ),
            None,
        )

    result = await browser.act_internal(
        _run_context().deps,
        BrowserActRequest(action="click", target="@e3", wait_for_url="**/dashboard"),
        run_script=fake_run,
    )
    assert isinstance(result, BrowserResult)
    assert result.success is True
    assert result.url == "https://x.test/dashboard"
    assert result.snapshot == "@e1 button Sign out"
    assert "wait --url '**/dashboard'" in captured["script"]


@pytest.mark.asyncio
async def test_a_shed_browser_surfaces_as_a_readable_error():
    async def fake_run(ctx, script, operation):
        # 137 is SIGKILL: what `browser_guard` sends when memory runs out.
        return "\n".join(["", _sentinel_in(script), "rc=137"]), None

    result = await browser.act_internal(
        _run_context().deps,
        BrowserActRequest(action="click", target="@e3"),
        run_script=fake_run,
    )
    assert result.success is False
    assert result.error is not None and "low on memory" in result.error


@pytest.mark.asyncio
async def test_a_read_asks_for_no_snapshot():
    """A read cannot change the page, so it should not pay for a fresh tree."""
    captured: dict[str, str] = {}

    async def fake_run(ctx, script, operation):
        captured["script"] = script
        sentinel = _sentinel_in(script)
        return "\n".join(["https://x.test", sentinel, "rc=0"]), None

    result = await browser.read_internal(
        _run_context().deps, BrowserReadRequest(what="url"), run_script=fake_run
    )
    assert result.success is True
    assert result.output == "https://x.test"
    assert "snapshot" not in captured["script"]


@pytest.mark.asyncio
async def test_browser_tools_are_registered_with_their_docstrings():
    run_ctx = _run_context()
    prepared = await pydantic_adapter.browser_toolset.for_run(run_ctx)
    async with prepared:
        tools = await prepared.get_tools(run_ctx)

    for tool in pydantic_adapter.BROWSER_TOOLS:
        assert tool.__name__ in tools
    assert tools["browser_open"].tool_def.description.startswith(
        "Open a page in the workspace's real Chromium"
    )
    assert (
        tools["browser_act"].tool_def.parameters_json_schema["properties"]["action"]
        is not None
    )
