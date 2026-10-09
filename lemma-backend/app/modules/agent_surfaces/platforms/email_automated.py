"""Whether an email was written by a machine rather than a person.

A bot that answers whoever writes to it, writing to a machine that answers
whoever writes to *it* -- an out-of-office, a bounce, a ticket system's
acknowledgement -- is a loop, and each turn of it is a model run the pod pays
for. RFC 3834 asks automatic responders to say so (``Auto-Submitted``), mailing
lists and bulk senders mark themselves (``Precedence``, ``List-Id``), and
delivery reports come from addresses that no person reads. Any of them, and the
mail is not answered as a contact's.
"""

from __future__ import annotations

from collections.abc import Mapping

#: ``Precedence`` values that name mail sent to many, or by a robot.
_BULK_PRECEDENCE = frozenset({"bulk", "junk", "list", "auto_reply"})

#: Headers some responders set instead of ``Auto-Submitted``.
_RESPONDER_HEADERS = ("x-autoreply", "x-autorespond", "x-autoresponder")

#: Local parts nobody reads: delivery reports and send-only addresses.
_MACHINE_LOCAL_PARTS = frozenset(
    {
        "mailer-daemon",
        "postmaster",
        "noreply",
        "no-reply",
        "no_reply",
        "donotreply",
        "do-not-reply",
        "do_not_reply",
        "bounce",
        "bounces",
    }
)


def automated_reason(headers: Mapping[str, str], sender: str | None) -> str | None:
    """Why this email reads as automatic, or ``None`` if a person may have sent it.

    ``headers`` are lower-cased names, as ``header_map`` returns them.
    """
    auto_submitted = headers.get("auto-submitted", "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return "auto_submitted"
    if headers.get("precedence", "").strip().lower() in _BULK_PRECEDENCE:
        return "bulk"
    if headers.get("list-id") or headers.get("list-unsubscribe"):
        return "mailing_list"
    if any(headers.get(name) for name in _RESPONDER_HEADERS):
        return "auto_reply"
    if machine_sender(sender):
        return "machine_sender"
    return None


def machine_sender(sender: str | None) -> bool:
    """Whether the address is one only machines send from."""
    local = (sender or "").partition("@")[0].strip().lower()
    if not local:
        return False
    base = local.split("+", 1)[0]
    return base in _MACHINE_LOCAL_PARTS or base.startswith(("noreply", "no-reply"))
