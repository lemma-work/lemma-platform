"""Deciders that ship with Lemma.

They are code rather than rows: versioned with releases, reviewed like code,
and available before any pod exists. Their examples are still learned per pod,
from the people who answer them there.
"""

from __future__ import annotations

from types import MappingProxyType

from app.modules.decisions.domain.deciders import DeciderDefinition, Lane

#: Prefix that marks a system decider's name wherever a name is accepted.
SYSTEM_PREFIX = "system:"

_REPLY_TO_QUESTION = DeciderDefinition.model_validate(
    {
        "description": (
            "Which option a person's typed reply picks, for a question Lemma "
            "asked them with options."
        ),
        "guidance": (
            "Lemma asked a person a question with a few options, and they typed "
            "a reply instead of tapping one. Pick the option the reply clearly "
            "chooses, by its number, its label or its meaning. A reply that "
            "answers in its own words with something that is none of the "
            "options is other_answer. A reply that asks something back, changes "
            "the subject, or does not answer is not_an_answer."
        ),
        "input": {"fields": ["question", "reply"], "max_chars": 4000},
        "questions": {
            "choice": {
                "type": "choice",
                "prompt": "Which option does the reply choose?",
                "options": {
                    "other_answer": (
                        "An answer to the question in the person's own words "
                        "that is none of the listed options."
                    ),
                    "not_an_answer": (
                        "Not an answer to the question: a question back, a "
                        "change of subject, or something unclear."
                    ),
                },
                "fallback": "not_an_answer",
            }
        },
        "policy": {"lane": Lane.INTERACTIVE, "escalate_to_model": False},
    }
)

#: Asked only for replies the exact approval phrases did not match, so the rules
#: rung is `classify_approval_reply` itself and is not repeated here. Trust is
#: asymmetric: an engine may always deny or pass the words on, may approve once
#: only with System One's confidence behind it, and may never approve for the
#: session -- that stays with the exact phrases.
_APPROVAL_REPLY = DeciderDefinition.model_validate(
    {
        "description": "Whether a person's typed reply to an approval request is consent.",
        "guidance": (
            "Lemma asked a person to approve one action and they typed a reply "
            "instead of tapping a button. Consent must be plain and "
            "unconditional. A reply that adds a condition, asks a question, "
            "hesitates, or talks about something else is not an answer, and "
            "the person's words will be passed on as a message."
        ),
        "input": {"fields": ["request", "reply"], "max_chars": 4000},
        "questions": {
            "decision": {
                "type": "choice",
                "prompt": "What does the reply say about the requested action?",
                "options": {
                    "approve_once": {
                        "description": "Plain, unconditional consent to this one action.",
                        "not_for": (
                            "Consent with a condition or a question attached, "
                            "such as 'yes, but only if...' or 'ok, why?'."
                        ),
                    },
                    "approve_for_session": (
                        "Consent to this action and everything like it for the "
                        "rest of the conversation."
                    ),
                    "deny": "A refusal, a cancellation, or an instruction to stop.",
                    "not_an_answer": (
                        "Anything else: a question, a condition, a correction, "
                        "hesitation or another request."
                    ),
                },
                "fallback": "not_an_answer",
            }
        },
        "policy": {
            "lane": Lane.INTERACTIVE,
            "escalate_to_model": False,
            "rules_only": {"decision": ["approve_for_session"]},
            "require_confidence": {"decision": {"approve_once": 0.9}},
        },
    }
)

SYSTEM_DECIDERS: MappingProxyType[str, DeciderDefinition] = MappingProxyType(
    {
        "reply_to_question": _REPLY_TO_QUESTION,
        "approval_reply": _APPROVAL_REPLY,
    }
)


def system_decider(name: str) -> DeciderDefinition | None:
    """The system decider called `name`, with or without the `system:` prefix."""
    return SYSTEM_DECIDERS.get(name.removeprefix(SYSTEM_PREFIX))
