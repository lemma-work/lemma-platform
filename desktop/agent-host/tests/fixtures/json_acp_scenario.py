"""Replay ACP wire messages with explicit client-response gates.

The fixture is a provider process, so all dispatch, streaming, permission and
persistence behavior on the other side still comes from the shipped app.
"""

import json
from pathlib import Path


def run_scenario(path, emit, receive, release):
    scenario = json.loads(Path(path).read_text(encoding="utf-8"))
    if scenario.get("version") != 1:
        raise ValueError("unsupported ACP scenario version")
    pending = []

    def await_message(expected):
        while True:
            for index, message in enumerate(pending):
                if all(message.get(key) == value for key, value in expected.items()):
                    return pending.pop(index)
            message = receive()
            if message is None:
                raise EOFError("host disconnected before the expected ACP response")
            pending.append(message)

    def steps(actions):
        for action in actions:
            if len(action) != 1:
                raise ValueError("each ACP step must have exactly one action")
            if "send" in action:
                emit(action["send"])
            elif "await_permission" in action:
                gate = action["await_permission"]
                response = await_message({"id": gate["id"]})
                outcome = response.get("result", {}).get("outcome", {})
                decision = outcome.get("outcome")
                if decision == "selected":
                    option = outcome.get("optionId")
                    selected = gate["selected"]
                    if option not in selected:
                        raise AssertionError(f"unexpected permission option: {option}")
                    steps(selected[option])
                elif decision == "cancelled":
                    steps(gate["cancelled"])
                else:
                    raise AssertionError(f"invalid ACP permission response: {response}")
            elif "await_cancel" in action:
                if action["await_cancel"] is not True:
                    raise ValueError("await_cancel must be true")
                await_message({"method": "session/cancel"})
            elif "await_release" in action:
                if action["await_release"] is not True:
                    raise ValueError("await_release must be true")
                release()
            elif "exit" in action:
                raise SystemExit(action["exit"])
            else:
                raise ValueError(f"unsupported ACP scenario action: {list(action)}")

    steps(scenario["steps"])
    return scenario.get("stopReason", "end_turn")
