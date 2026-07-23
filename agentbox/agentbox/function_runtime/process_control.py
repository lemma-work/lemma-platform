from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
from uuid import UUID

from agentbox.function_runtime.process_protocol import (
    CancelRequest,
    ProcessInspection,
    ProcessStateRecord,
    RuntimeOutputChunk,
)


_ROOT = Path("/tmp/.agentbox/processes")
_INPUT_ENV = "AGENTBOX_PROCESS_INPUT"


def _operation_directory(operation_id: UUID) -> Path:
    return _ROOT / str(operation_id)


def _inspect(operation_id: UUID, after_sequence: int) -> None:
    directory = _operation_directory(operation_id)
    state_path = directory / "state.json"
    state = (
        ProcessStateRecord.model_validate_json(state_path.read_bytes())
        if state_path.exists()
        else None
    )
    chunks: list[RuntimeOutputChunk] = []
    output_directory = directory / "output"
    if output_directory.exists():
        for path in sorted(output_directory.glob("*.bin")):
            sequence_text, channel = path.stem.split("-", 1)
            sequence = int(sequence_text)
            if sequence < after_sequence:
                continue
            chunks.append(
                RuntimeOutputChunk(
                    sequence=sequence,
                    channel=channel,
                    data_base64=base64.b64encode(path.read_bytes()).decode(),
                )
            )
    print(ProcessInspection(state=state, chunks=tuple(chunks)).model_dump_json())


def _input(operation_id: UUID, name: str) -> None:
    encoded = os.environ.pop(_INPUT_ENV)
    data = base64.b64decode(encoded, validate=True)
    directory = _operation_directory(operation_id) / "input"
    directory.mkdir(parents=True, exist_ok=True)
    consumed = _operation_directory(operation_id) / "consumed-input" / f"{name}.bin"
    temporary = directory / f".{name}.tmp"
    target = directory / f"{name}.bin"
    if target.exists() or consumed.exists():
        return
    temporary.write_bytes(data)
    os.replace(temporary, target)


def _cancel(operation_id: UUID, grace_seconds: float) -> None:
    directory = _operation_directory(operation_id) / "control"
    directory.mkdir(parents=True, exist_ok=True)
    request = CancelRequest(grace_seconds=grace_seconds)
    temporary = directory / ".cancel.tmp"
    target = directory / "cancel.json"
    temporary.write_text(request.model_dump_json(), encoding="utf-8")
    os.replace(temporary, target)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("operation_id", type=UUID)
    inspect_parser.add_argument("after_sequence", type=int)
    input_parser = commands.add_parser("input")
    input_parser.add_argument("operation_id", type=UUID)
    input_parser.add_argument("name")
    cancel_parser = commands.add_parser("cancel")
    cancel_parser.add_argument("operation_id", type=UUID)
    cancel_parser.add_argument("grace_seconds", type=float)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "inspect":
        _inspect(args.operation_id, args.after_sequence)
    elif args.command == "input":
        _input(args.operation_id, args.name)
    else:
        _cancel(args.operation_id, args.grace_seconds)


if __name__ == "__main__":
    main()
