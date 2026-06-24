from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from ..io import emit
from ..payload import read_json
from ..state import fail, run_with_client, state_from_ctx

app = typer.Typer(help="Standalone Lemma tool commands.")

TOOL_NAMES = {
    "web-search",
    "report-feedback",
}


@app.command("list")
def list_tools(ctx: typer.Context) -> None:
    """List available tools."""
    state = state_from_ctx(ctx)
    emit(
        state,
        {
            "items": [
                {
                    "name": "web-search",
                    "description": "Run a raw web search and return structured results.",
                    "payload": {"query": "string", "max_results": "integer?"},
                },
                {
                    "name": "report-feedback",
                    "description": "Submit CLI, skill, or platform feedback.",
                    "payload": {
                        "category": "string",
                        "subject": "string",
                        "issue_encountered": "string",
                        "expected_behavior": "string",
                        "actual_behavior": "string",
                        "suggested_next_steps": "string?",
                    },
                },
            ]
        },
    )


@app.command("run")
def run_tool(
    ctx: typer.Context,
    tool: str = typer.Argument(...),
    json_payload: str | None = typer.Option(None, "--data", "-d", help="Raw JSON payload."),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Run a tool with a JSON payload."""
    payload = read_json(json_payload, file, required=True)
    _emit_tool_result(ctx, tool=tool, payload=payload)


@app.command("web-search")
def web_search(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Run a web search."""
    _emit_tool_result(
        ctx,
        tool="web-search",
        payload={"query": query, "max_results": limit},
    )


@app.command("report-feedback")
def report_feedback(
    ctx: typer.Context,
    category: str = typer.Option(..., "--category"),
    subject: str = typer.Option(..., "--subject"),
    issue_encountered: str = typer.Option(..., "--issue-encountered"),
    expected_behavior: str = typer.Option(..., "--expected-behavior"),
    actual_behavior: str = typer.Option(..., "--actual-behavior"),
    suggested_next_steps: str | None = typer.Option(None, "--suggested-next-steps"),
) -> None:
    """Send product feedback."""
    payload = {
        "category": category,
        "subject": subject,
        "issue_encountered": issue_encountered,
        "expected_behavior": expected_behavior,
        "actual_behavior": actual_behavior,
        "suggested_next_steps": suggested_next_steps,
    }
    _emit_tool_result(ctx, tool="report-feedback", payload=payload)


def _emit_tool_result(ctx: typer.Context, *, tool: str, payload: dict[str, Any]) -> None:
    normalized = tool.lower().strip().replace("_", "-")
    if normalized not in TOOL_NAMES:
        fail(f"Unknown tool: {tool}. Use one of: {', '.join(sorted(TOOL_NAMES))}.")

    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: _run_tool(client, normalized, payload),
    )
    if result is not None:
        emit(state, result)


def _run_tool(client: Any, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool == "web-search":
        query = str(payload.get("query") or "")
        if not query:
            raise ValueError("web-search requires payload.query.")
        return client.tools.web_search(
            query=query,
            max_results=int(payload.get("max_results") or payload.get("limit") or 10),
        )
    return client.tools.report_feedback(
        category=str(payload.get("category") or ""),
        subject=str(payload.get("subject") or ""),
        issue_encountered=str(payload.get("issue_encountered") or ""),
        expected_behavior=str(payload.get("expected_behavior") or ""),
        actual_behavior=str(payload.get("actual_behavior") or ""),
        suggested_next_steps=payload.get("suggested_next_steps"),
    )
