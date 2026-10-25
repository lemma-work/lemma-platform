"""A voice built from parts: Deepgram listens, a Lemma model answers, Deepgram speaks.

The parts are interchangeable; what this file owns is the turn between them.

- **Drafting early.** On Flux's eager end of turn the talker starts writing, and
  nothing is spoken until the end of turn is confirmed. If the caller resumes,
  the draft is dropped; if the confirmed transcript differs, it is redone.
- **Barge-in.** The caller starting to talk while the voice is speaking stops
  the reply, clears what Aura has not said, and drops audio already in flight.
- **What was heard.** The history the talker sees records the reply only as far
  as it was sent to be spoken, so the model is never told it said a sentence
  the caller cut off.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from openai import APIError
from pydantic_ai.exceptions import AgentRunError
from websockets.exceptions import ConnectionClosed

from app.modules.calls.domain.frames import (
    EVENT_BACKLOG,
    offer,
)
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.calls.domain.voice import (
    AudioOut,
    EngineClosed,
    EngineUnavailable,
    Heard,
    Inject,
    Interrupted,
    Said,
    TurnDone,
    UserSpeaking,
    VoiceEvent,
)
from app.modules.calls.infrastructure.engines.deepgram import (
    SPEAK_SAMPLE_RATE,
    AuraSpeaker,
    FluxListener,
)
from app.modules.calls.infrastructure.engines.talker import Line
from app.modules.usage.contracts import UsageLimitExceededError

logger = get_logger(__name__)

#: A sentence end, or a clause break once a sentence is long enough to say.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")
_LONG_CLAUSE_CHARS = 140
#: The history the talker gets; older lines are dropped, the agents keep them.
_HISTORY_LINES = 40
_FLUSH_TIMEOUT_SECONDS = 30.0
#: Said when the talker or the speaker fails a reply, so the caller is not left
#: waiting on a silence that will not end.
_SORRY = "Sorry, I couldn't answer that just now. Could you say it again?"
_NOTE_PROMPT = (
    "Your team just sent this for the caller. Tell them now, briefly, in your "
    "own words:\n"
)


class Talker(Protocol):
    def reply(self, history: Sequence[Line], prompt: str) -> AsyncIterator[str]: ...


class _Reply:
    """One reply being written, and whether it may be spoken yet."""

    def __init__(
        self, prompt: str, *, committed: bool, caller_line: str | None
    ) -> None:
        self.prompt = prompt
        self.caller_line = caller_line
        self.committed = asyncio.Event()
        if committed:
            self.committed.set()
        self.spoken: list[str] = []
        self.task: asyncio.Task[None] | None = None


class CascadeEngine:
    output_sample_rate = SPEAK_SAMPLE_RATE

    def __init__(
        self, *, listener: FluxListener, speaker: AuraSpeaker, talker: Talker
    ) -> None:
        self._listener = listener
        self._speaker = speaker
        self._talker = talker
        self._events: asyncio.Queue[VoiceEvent] = asyncio.Queue(maxsize=EVENT_BACKLOG)
        self._history: list[Line] = []
        #: The newest reply: a draft, or the next one to speak.
        self._reply: _Reply | None = None
        #: The reply holding the floor. Usually the newest, but not while a team
        #: update waits behind it, and a barge-in must stop this one.
        self._speaking: _Reply | None = None
        self._turn = asyncio.Lock()
        self._dropping_audio = False
        #: The caller's words so far this turn. Flux sends the whole turn on
        #: every update; the session is sent only what is new.
        self._heard_so_far = ""
        self._tasks: list[asyncio.Task[None]] = []

    async def open(self) -> None:
        await asyncio.gather(self._listener.open(), self._speaker.open())
        self._tasks = [
            create_inherited_task(self._listen(), name="calls.cascade.listen"),
            create_inherited_task(self._play(), name="calls.cascade.play"),
        ]

    async def push_audio(self, pcm: bytes) -> None:
        await self._listener.send(pcm)

    async def inject(self, text: str, mode: Inject) -> None:
        self._history.append(Line("note", text))
        if mode is Inject.SPEAK:
            self._start(_NOTE_PROMPT + text, committed=True, caller_line=None)

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, EngineClosed):
                return

    async def close(self) -> None:
        await self._cancel_reply()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await asyncio.gather(self._listener.close(), self._speaker.close())

    # -- listening ---------------------------------------------------------

    async def _listen(self) -> None:
        try:
            async for turn in self._listener.turns():
                match turn.event:
                    case "StartOfTurn":
                        self._heard_so_far = ""
                        offer(self._events, UserSpeaking())
                        if self._speaking is not None or (
                            self._reply is not None and self._reply.committed.is_set()
                        ):
                            await self._interrupt()
                    case "Update":
                        self._heard_more(turn.transcript)
                    case "EagerEndOfTurn":
                        self._start(
                            turn.transcript,
                            committed=False,
                            caller_line=turn.transcript,
                        )
                    case "TurnResumed":
                        if (
                            self._reply is not None
                            and not self._reply.committed.is_set()
                        ):
                            await self._cancel_reply()
                    case "EndOfTurn":
                        self._end_of_turn(turn.transcript)
        except EngineUnavailable:
            # The speaker could not reconnect while the caller barged in.
            logger.warning("calls.cascade.listen.failed", exc_info=True)
        finally:
            # Whatever stopped listening, the call is told: a session left
            # waiting on events would hold a silent call open.
            offer(
                self._events,
                EngineClosed("Speech recognition disconnected."),
                essential=True,
            )

    def _heard_more(self, transcript: str) -> None:
        # A revision ("three menu" -> "three venues") is not sent as a
        # fragment: the final transcript replaces the line when the turn ends.
        if not transcript.startswith(self._heard_so_far):
            return
        new = transcript[len(self._heard_so_far) :]
        self._heard_so_far = transcript
        if new:
            offer(self._events, Heard(new, final=False))

    def _end_of_turn(self, transcript: str) -> None:
        self._heard_so_far = ""
        if not transcript.strip():
            return
        offer(self._events, Heard(transcript, final=True))
        draft = self._reply
        if draft is not None and not draft.committed.is_set():
            if _same(draft.prompt, transcript):
                draft.committed.set()
                return
            if draft.task is not None:
                draft.task.cancel()
        self._start(transcript, committed=True, caller_line=transcript)

    # -- replying ----------------------------------------------------------

    def _start(self, prompt: str, *, committed: bool, caller_line: str | None) -> None:
        earlier = self._reply
        if (
            earlier is not None
            and earlier.task is not None
            and not earlier.committed.is_set()
        ):
            # A newer draft replaces an unspoken one.
            earlier.task.cancel()
        reply = _Reply(prompt, committed=committed, caller_line=caller_line)
        reply.task = create_inherited_task(self._run(reply), name="calls.cascade.reply")
        self._reply = reply

    async def _run(self, reply: _Reply) -> None:
        async with self._turn:
            if self._reply is not reply and not reply.committed.is_set():
                return
            history = self._history[-_HISTORY_LINES:]
            self._speaking = reply
            try:
                await self._write_and_speak(reply, history)
            except asyncio.CancelledError:
                self._record(reply, interrupted=True)
                raise
            except (
                AgentRunError,
                APIError,
                UsageLimitExceededError,
                ConnectionClosed,
                EngineUnavailable,
                OSError,
                TimeoutError,
            ):
                # The talker or the speaker failed for this reply. The call
                # survives it: the caller can say it again.
                logger.warning("calls.cascade.reply.failed", exc_info=True)
                self._record(reply, interrupted=True)
                await self._apologise()
                return
            finally:
                if self._speaking is reply:
                    self._speaking = None
            self._record(reply, interrupted=False)

    async def _write_and_speak(self, reply: _Reply, history: Sequence[Line]) -> None:
        pending = ""
        # Closed here, not by a finalizer: a reply cancelled mid-stream must
        # unwind the talker's run in this task, where its context was set.
        async with contextlib.aclosing(
            self._talker.reply(history, reply.prompt)
        ) as deltas:
            async for delta in deltas:
                pending += delta
                sentences, pending = _split(pending)
                for sentence in sentences:
                    await self._say(reply, sentence)
        if pending.strip():
            await self._say(reply, pending.strip())
        if not reply.spoken:
            return
        async with self._speaker.flushed:
            target = self._speaker.flush_count + 1
            await self._speaker.flush()
            async with asyncio.timeout(_FLUSH_TIMEOUT_SECONDS):
                await self._speaker.flushed.wait_for(
                    lambda: self._speaker.flush_count >= target
                )

    async def _say(self, reply: _Reply, sentence: str) -> None:
        await reply.committed.wait()
        if not reply.spoken:
            # The first words of a new reply: audio from here on is wanted.
            self._dropping_audio = False
        reply.spoken.append(sentence)
        offer(self._events, Said(sentence + " ", final=False))
        await self._speaker.speak(sentence)

    async def _apologise(self) -> None:
        self._dropping_audio = False
        # The speaker may be what failed; then the caption is all there is.
        with contextlib.suppress(ConnectionClosed, EngineUnavailable, OSError):
            await self._speaker.speak(_SORRY)
            await self._speaker.flush()
        self._history.append(Line("voice", _SORRY))
        offer(self._events, Said(_SORRY, final=True))

    def _record(self, reply: _Reply, *, interrupted: bool) -> None:
        if not reply.committed.is_set():
            return
        if reply.caller_line:
            self._history.append(Line("caller", reply.caller_line))
        said = " ".join(reply.spoken).strip()
        if said:
            self._history.append(Line("voice", said + (" —" if interrupted else "")))
            offer(self._events, Said(said, final=True))
        if self._reply is reply:
            self._reply = None
        offer(self._events, TurnDone())

    async def _interrupt(self) -> None:
        # Silence first: audio already in flight is dropped, and Aura told to
        # stop, before anything else about the turn is settled.
        self._dropping_audio = True
        await self._speaker.clear()
        await self._cancel_reply()
        offer(self._events, Interrupted())

    async def _cancel_reply(self) -> None:
        """Stop the reply speaking and any waiting behind it."""
        replies = {id(r): r for r in (self._speaking, self._reply) if r is not None}
        self._reply = self._speaking = None
        for reply in replies.values():
            if reply.task is None:
                continue
            reply.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reply.task

    # -- playing -----------------------------------------------------------

    async def _play(self) -> None:
        async for pcm in self._speaker.audio():
            if not self._dropping_audio:
                offer(self._events, AudioOut(pcm, SPEAK_SAMPLE_RATE))


def _split(text: str) -> tuple[list[str], str]:
    """Whole sentences ready to speak, and the unfinished rest."""
    parts = _SENTENCE_END.split(text)
    done = [part.strip() for part in parts[:-1] if part.strip()]
    rest = parts[-1]
    if len(rest) > _LONG_CLAUSE_CHARS and "," in rest:
        head, _, tail = rest.rpartition(",")
        done.append(head.strip() + ",")
        rest = tail
    return done, rest


def _same(draft: str, final: str) -> bool:
    def words(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    return words(draft) == words(final)
