"""SSE event-stream generator: snapshot-first, seq-dedup, terminal close."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4


from app.modules.pod_bundle.api.controllers.import_controller import (
    import_event_stream,
)
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportPlan,
    ImportState,
    ImportStatus,
)


class FakeStore:
    def __init__(self, state):
        self._state = state

    async def get_import(self, import_id):
        return self._state


class FakeChannel:
    def __init__(self, messages):
        self._messages = messages

    @asynccontextmanager
    async def subscribe(self, channels):
        async def _iter():
            for m in self._messages:
                yield m

        yield _iter()


def _state(pod_id, *, seq=3, status=ImportStatus.AWAITING_CONFIRMATION) -> ImportState:
    s = ImportState(
        import_id=uuid4(),
        pod_id=pod_id,
        user_id=uuid4(),
        source=BundleSource(kind="upload"),
        status=status,
        plan=ImportPlan(format_version=2, steps=[]),
    )
    s.seq = seq
    return s


def _frames(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        line = chunk.strip()
        assert line.startswith("data:")
        out.append(json.loads(line[len("data:"):].strip()))
    return out


async def _collect(gen) -> list[str]:
    return [chunk async for chunk in gen]


async def test_first_frame_is_snapshot_then_live_frames():
    pod_id = uuid4()
    state = _state(pod_id, seq=3)
    channel = FakeChannel(
        [
            json.dumps({"type": "status", "status": "APPLYING", "seq": 2}),  # stale, dropped
            json.dumps({"type": "step", "seq": 4}),
            json.dumps({"type": "completed", "status": "COMPLETED", "seq": 5}),
            json.dumps({"type": "step", "seq": 6}),  # never reached (closed on completed)
        ]
    )
    frames = _frames(await _collect(import_event_stream(FakeStore(state), channel, pod_id, state.import_id)))

    assert frames[0]["type"] == "snapshot"
    assert frames[0]["seq"] == 3
    assert frames[0]["state"]["plan"]["format_version"] == 2
    # seq<=3 dropped; then step(4), completed(5); stream stops at the terminal event.
    assert [f["type"] for f in frames[1:]] == ["step", "completed"]


async def test_expired_when_state_missing():
    pod_id = uuid4()
    frames = _frames(
        await _collect(import_event_stream(FakeStore(None), FakeChannel([]), pod_id, uuid4()))
    )
    assert frames == [{"type": "expired"}]


async def test_pod_mismatch_is_expired():
    state = _state(uuid4())
    frames = _frames(
        await _collect(
            import_event_stream(FakeStore(state), FakeChannel([]), uuid4(), state.import_id)
        )
    )
    assert frames == [{"type": "expired"}]


async def test_terminal_state_closes_after_snapshot():
    pod_id = uuid4()
    state = _state(pod_id, status=ImportStatus.COMPLETED)
    channel = FakeChannel([json.dumps({"type": "step", "seq": 99})])
    frames = _frames(
        await _collect(import_event_stream(FakeStore(state), channel, pod_id, state.import_id))
    )
    # An already-terminal import emits only the snapshot; no live frames follow.
    assert len(frames) == 1 and frames[0]["type"] == "snapshot"
