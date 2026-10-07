from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from ..io import emit, to_plain
from ..payload import STDIN_TOKEN, read_json
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx

app = typer.Typer(help="Decision commands: closed questions about some evidence.")

_EXAMPLE = """\
  lemma decision run -i "Triage this support email." -e "Charged twice, refund please" \\
    --schema '{"type":"object","properties":{"urgent":{"type":"boolean","description":"Reply today?"}}}'
  cat email.txt | lemma decision run -i "Triage this email." -e - --schema-file questions.json"""


@app.command("run", epilog=f"Examples:\n\n{_EXAMPLE}")
def run_decision(
    ctx: typer.Context,
    instruction: str | None = typer.Option(
        None, "--instruction", "-i", help="What to judge and how. Trusted."
    ),
    evidence: str | None = typer.Option(
        None,
        "--evidence",
        "-e",
        help="What to judge, as text; `-` reads it from stdin. Never followed as instructions.",
    ),
    evidence_file: Path | None = typer.Option(
        None,
        "--evidence-file",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Read the evidence from a file; a .json file is sent as JSON.",
    ),
    schema: str | None = typer.Option(
        None,
        "--schema",
        help="The questions, as a flat JSON Schema object (one property per question).",
    ),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", exists=True, dir_okay=False, readable=True
    ),
    priority: str | None = typer.Option(
        None,
        "--priority",
        help="`interactive` when someone is waiting on the answer, else `background`.",
    ),
    json_payload: str | None = typer.Option(
        None,
        "--data",
        "-d",
        help="The whole request as JSON: instruction, evidence, schema, examples, priority.",
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Answer closed questions about some evidence; nothing is stored."""
    request = _request(
        read_json(json_payload, file),
        instruction=instruction,
        evidence=_evidence(
            evidence, evidence_file, from_payload=json_payload == STDIN_TOKEN
        ),
        schema=_schema(schema, schema_file),
        priority=priority,
    )
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: to_plain(
            pod_client(client, s, pod).decisions.make(**request)
        ),
    )
    if result is not None:
        emit(state, result)


def _request(
    payload: dict[str, Any],
    *,
    instruction: str | None,
    evidence: Any,
    schema: dict[str, Any] | None,
    priority: str | None,
) -> dict[str, Any]:
    """The payload from --data/--file, with any flag given taking precedence."""
    merged = dict(payload)
    for key, value in (
        ("instruction", instruction),
        ("evidence", evidence),
        ("schema", schema),
        ("priority", priority),
    ):
        if value is not None:
            merged[key] = value
    missing = [
        key for key in ("instruction", "evidence", "schema") if key not in merged
    ]
    if missing:
        raise typer.BadParameter(
            "Missing "
            + ", ".join(missing)
            + ". Pass --instruction, --evidence (or --evidence-file) and --schema "
            "(or --schema-file), or the whole request with --data/--file."
        )
    unknown = sorted(
        set(merged) - {"instruction", "evidence", "schema", "examples", "priority"}
    )
    if unknown:
        raise typer.BadParameter(f"Unknown request field(s): {', '.join(unknown)}.")
    # A list or object from --data would make the membership test itself raise.
    if merged.get("priority", "background") not in ("interactive", "background"):
        raise typer.BadParameter("--priority is `interactive` or `background`.")
    return merged


def _evidence(text: str | None, path: Path | None, *, from_payload: bool) -> Any:
    if text is not None and path is not None:
        raise typer.BadParameter("Use only one of --evidence or --evidence-file.")
    if path is not None:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() != ".json":
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"Invalid JSON in {path}: {exc}") from exc
    if text == STDIN_TOKEN:
        if from_payload:
            raise typer.BadParameter(
                "Only one of --data and --evidence can read stdin."
            )
        return sys.stdin.read()
    return text


def _schema(text: str | None, path: Path | None) -> dict[str, Any] | None:
    if text is not None and path is not None:
        raise typer.BadParameter("Use only one of --schema or --schema-file.")
    if text is None and path is None:
        return None
    raw = path.read_text(encoding="utf-8") if path is not None else str(text)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Invalid JSON schema: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("The schema must be a JSON object.")
    return parsed
