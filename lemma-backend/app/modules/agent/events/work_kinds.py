"""The agent module's work kinds, by name, before their handlers exist.

Agent turns, approvals and waits move onto the work runtime in separate
pieces of work that land in either order. Each names the kinds -- and builds
the dedup keys -- through these constants, so a writer (``WorkWriter.add``),
a canceller (``WorkWriter.cancel``) and the handler registration can never
disagree on a string. Constants only: importing this module registers nothing.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

#: One turn of a conversation: runs the harness until the run ends or pauses.
AGENT_TURN: Final = "agent.turn"
#: The same turn for a run nobody is watching live (a schedule, a workflow
#: step, a trigger): one kind per class, because a kind has one class. Both
#: share the turn's dedup and concurrency keys, so a conversation still runs
#: one turn at a time whichever class it landed in.
AGENT_TURN_AUTOMATION: Final = "agent.turn.automation"
#: Both turn kinds, for the writers that must reach a turn whatever its class.
AGENT_TURN_KINDS: Final = (AGENT_TURN, AGENT_TURN_AUTOMATION)
#: Names a conversation after its first exchange.
AGENT_TITLE: Final = "agent.title"
#: Starts the run for messages queued while the previous one was busy.
AGENT_FOLLOWUP: Final = "agent.followup"
#: Hands a finished sub-agent's result to a parent that is not waiting on it.
AGENT_SUBAGENT_RESULT: Final = "agent.subagent_result"
#: Brings an asking conversation back once every notification it sent settled.
AGENT_REPLIES_ARRIVED: Final = "agent.replies_arrived"
#: Executes an approved tool call and resumes the conversation.
AGENT_APPROVAL_EXECUTE: Final = "agent.approval.execute"
#: A ``wait_for`` timer: fires a TIME wait, or a SUBAGENT wait's deadline.
AGENT_WAIT_FIRE: Final = "agent.wait.fire"
#: A PROCESS wait's poll: reads the sandbox, so a JOB rather than FAST, and
#: re-arms itself until the process ends. Same dedup key as the timer.
AGENT_WAIT_POLL: Final = "agent.wait.poll"
#: Resolves a parent's SUBAGENT wait when the child run finishes.
AGENT_WAIT_RESOLVE_CHILD: Final = "agent.wait.resolve_child"
#: Delivers a pod-to-pod ask's answer into the asking pod.
POD_ASK_DELIVER: Final = "pod_ask.deliver"
#: Fans a member's line in Lemma out to one answer item per answering agent.
AGENT_CHANNEL_LINE: Final = "agent.channel_line"
#: Starts the turn one agent answers one member's line in Lemma with.
AGENT_CHANNEL_ANSWER: Final = "agent.channel_answer"
#: Posts a finished run's answer to the line in Lemma it answers.
AGENT_CHANNEL_REPLY: Final = "agent.channel_reply"
#: After a run: brings a conversation's pause questions in line with what it
#: still waits on, and seals the run's batch of questions to people.
AGENT_PAUSE_QUESTIONS: Final = "agent.pause_questions"
#: A paused ``ask_user`` / ``request_approval`` whose question's time ran out:
#: the run is told nobody answered, and resumes.
AGENT_PAUSE_EXPIRED: Final = "agent.pause_expired"
#: A question one conversation put to a member closed: is its batch settled?
AGENT_ASKED_QUESTION_CLOSED: Final = "agent.asked_question_closed"
#: After a run a person was waiting on: keeps what was worth remembering from
#: it, which the run itself was told not to stop and save.
AGENT_MEMORY: Final = "agent.memory"


def turn_key(run_id: UUID) -> str:
    """The ``agent.turn`` dedup key: stop cancels the queued turn by it."""
    return f"agent-run:{run_id}"


def conversation_key(conversation_id: UUID) -> str:
    """The turn's concurrency key: one turn of a conversation runs at a time."""
    return f"conversation:{conversation_id}"


def title_key(conversation_id: UUID) -> str:
    return f"conv-title:{conversation_id}"


def followup_key(run_id: UUID) -> str:
    return f"followup:{run_id}"


def subagent_result_key(run_id: UUID) -> str:
    return f"subagent-result:{run_id}"


def approval_key(conversation_id: UUID, approval_id: UUID | str) -> str:
    """Both racing deciders write this; the queued item coalesces them.

    ``approval_id`` is the pausing call's tool call id, which a model provider
    mints and need not be a UUID.
    """
    return f"agent-approval:{conversation_id}:{approval_id}"


def wait_key(wait_id: UUID) -> str:
    """The ``agent.wait.fire`` dedup key: cancelling a wait drops its timer by it."""
    return f"wait:{wait_id}"


def child_wait_key(run_id: UUID) -> str:
    return f"child-wait:{run_id}"


def pod_ask_key(run_id: UUID) -> str:
    return f"pod-ask:{run_id}"


def replies_key(batch_id: str) -> str:
    """One wake-up per batch of questions sent together."""
    return f"replies-arrived:{batch_id}"


def channel_line_key(message_id: UUID) -> str:
    return f"channel-line:{message_id}"


def channel_answer_key(message_id: UUID, agent_id: UUID) -> str:
    """One answer per line and agent, however often the line is announced."""
    return f"channel-answer:{message_id}:{agent_id}"


def channel_reply_key(run_id: UUID) -> str:
    return f"channel-reply:{run_id}"


def pause_questions_key(run_id: UUID) -> str:
    return f"pause-questions:{run_id}"


def pause_expired_key(message_id: UUID) -> str:
    return f"pause-expired:{message_id}"


def asked_question_closed_key(message_id: UUID) -> str:
    return f"asked-question-closed:{message_id}"


def memory_pass_key(run_id: UUID) -> str:
    return f"memory-pass:{run_id}"


def memory_pod_key(pod_id: UUID) -> str:
    """One memory pass at a time per pod: two would rewrite the shared index
    each from what it read before the other wrote."""
    return f"memory:{pod_id}"


__all__ = [
    "AGENT_APPROVAL_EXECUTE",
    "AGENT_ASKED_QUESTION_CLOSED",
    "AGENT_CHANNEL_ANSWER",
    "AGENT_CHANNEL_LINE",
    "AGENT_CHANNEL_REPLY",
    "AGENT_PAUSE_EXPIRED",
    "AGENT_PAUSE_QUESTIONS",
    "AGENT_FOLLOWUP",
    "AGENT_MEMORY",
    "AGENT_REPLIES_ARRIVED",
    "AGENT_SUBAGENT_RESULT",
    "AGENT_TITLE",
    "AGENT_TURN",
    "AGENT_TURN_AUTOMATION",
    "AGENT_TURN_KINDS",
    "AGENT_WAIT_FIRE",
    "AGENT_WAIT_POLL",
    "AGENT_WAIT_RESOLVE_CHILD",
    "POD_ASK_DELIVER",
    "approval_key",
    "asked_question_closed_key",
    "channel_answer_key",
    "channel_line_key",
    "channel_reply_key",
    "child_wait_key",
    "conversation_key",
    "followup_key",
    "memory_pass_key",
    "memory_pod_key",
    "pause_expired_key",
    "pause_questions_key",
    "pod_ask_key",
    "replies_key",
    "subagent_result_key",
    "title_key",
    "turn_key",
    "wait_key",
]
