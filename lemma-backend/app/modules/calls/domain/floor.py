"""Who has the floor, and when an update may be spoken.

Pure: time is passed in. The session feeds it what the caller and the voice are
doing and asks it what to say next.

Updates the agents want said wait here for a pause rather than relying on a
provider's own "when idle" scheduling, which was seen dropping results that
arrived while the voice was mid-sentence. A pause is both sides quiet for the
quiet window; an urgent update only waits for the caller to stop talking, never
for the voice, because the voice can be cut off and the caller should not be.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

#: Updates past this many are dropped, oldest first, and a waiting update
#: that can wait goes before an urgent one.
MAX_QUEUED = 4
#: A side that has made no sound for this long has finished, whether or not
#: its engine said so: a model can take an update and choose not to answer,
#: and a caller's half-transcribed cough may never get a final transcript. A
#: floor that waited on either would never speak again.
VOICE_SILENT_MS = 8000
#: An urgent update waits this long for the caller to stop at most. A room
#: whose noise keeps the caller "speaking" would otherwise hold a failure the
#: person needs to hear for the rest of the call.
URGENT_WAIT_MS = 20_000


class Urgency(StrEnum):
    #: At the next pause.
    WHEN_IDLE = "when_idle"
    #: As soon as the caller is not talking.
    NOW = "now"


@dataclass(slots=True)
class Floor:
    quiet_window_ms: int
    caller_speaking: bool = False
    voice_speaking: bool = False
    last_activity_ms: float = 0.0
    #: Waiting updates, with when each was queued (None: not known).
    _queue: deque[tuple[str, Urgency, float | None]] = field(default_factory=deque)

    def caller_started(self, now_ms: float) -> None:
        self.caller_speaking = True
        self.last_activity_ms = now_ms

    def caller_finished(self, now_ms: float) -> None:
        self.caller_speaking = False
        self.last_activity_ms = now_ms

    def voice_active(self, now_ms: float) -> None:
        self.voice_speaking = True
        self.last_activity_ms = now_ms

    def voice_finished(self, now_ms: float) -> None:
        self.voice_speaking = False
        self.last_activity_ms = now_ms

    def queue(
        self, text: str, urgency: Urgency, now_ms: float | None = None
    ) -> list[str]:
        """Queue an update; returns any it had to drop to make room, so the
        caller can say so -- a dropped update is otherwise silent."""
        self._queue.append((text, urgency, now_ms))
        dropped: list[str] = []
        while len(self._queue) > MAX_QUEUED:
            idle = next(
                (entry for entry in self._queue if entry[1] is Urgency.WHEN_IDLE),
                None,
            )
            if idle is None:
                dropped.append(self._queue.popleft()[0])
            else:
                self._queue.remove(idle)
                dropped.append(idle[0])
        return dropped

    @property
    def waiting(self) -> int:
        return len(self._queue)

    def take(self, now_ms: float) -> str | None:
        """The update to speak now, or None while it must wait.

        Several waiting updates go out as one, so the voice says them in one
        breath instead of announcing each in turn.
        """
        if now_ms - self.last_activity_ms >= VOICE_SILENT_MS:
            self.caller_speaking = self.voice_speaking = False
        if not self._queue:
            return None
        if self.caller_speaking and not self._overdue(now_ms):
            return None
        urgent = any(urgency is Urgency.NOW for _, urgency, _ in self._queue)
        quiet = now_ms - self.last_activity_ms >= self.quiet_window_ms
        if not urgent and (self.voice_speaking or not quiet):
            return None
        texts = [text for text, _, _ in self._queue]
        self._queue.clear()
        self.voice_speaking = True
        self.last_activity_ms = now_ms
        return texts[0] if len(texts) == 1 else "\n\n".join(texts)

    def _overdue(self, now_ms: float) -> bool:
        return any(
            urgency is Urgency.NOW
            and queued_at is not None
            and now_ms - queued_at >= URGENT_WAIT_MS
            for _, urgency, queued_at in self._queue
        )
