"""Whether an inbound email's ``From:`` is the sender it claims to be.

A chat platform asserts who sent a message inside a payload we verified the
signature of. Email does not: ``From:`` is a line of text the sender chose, and
verifying the Resend webhook proves only that Resend delivered it. Since an
inbound address resolves to a Lemma user by that line, and the agent then runs
with that user's authority, the line has to be checked before it is believed.

The check is ``Authentication-Results`` (RFC 8601), written by the receiving
mail service after it evaluated SPF, DKIM and DMARC. Three outcomes, because
"the header is absent" is genuinely different from "the header says no":

* ``PASS``    — DMARC passed, or SPF/DKIM passed *aligned to the From domain*.
* ``FAIL``    — the receiver evaluated it and it did not pass. Never believed.
* ``UNKNOWN`` — no usable header. The provider may not add one; policy decides.

**The one subtlety worth knowing.** Anyone can put an ``Authentication-Results``
header in a message they send. The receiving service is supposed to strip
untrusted copies and prepend its own, so the *first* occurrence is the one it
wrote — which is why this reads every occurrence itself rather than going
through ``header_map``, whose dict is last-wins and would hand an attacker's
forged copy the final say. Where the deployment names its receiver's
``authserv-id``, only headers bearing that id are read at all, which is the
guarantee RFC 8601 actually intends.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

__all__ = [
    "EmailAuthenticationVerdict",
    "authentication_results_values",
    "evaluate_email_authentication",
]


class EmailAuthenticationVerdict(StrEnum):
    """What the receiving mail service concluded about the ``From:`` line."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


_HEADER = "authentication-results"
# `method=result`, e.g. `dmarc=pass`, at the start of a resinfo clause.
_METHOD = re.compile(r"\b(dmarc|dkim|spf)\s*=\s*([a-z]+)", re.IGNORECASE)
# `ptype.property=value`, e.g. `header.from=example.com`.
_PROPERTY = re.compile(r"\b((?:header|smtp|policy)\.[a-z]+)\s*=\s*([^\s;()]+)", re.I)
_COMMENT = re.compile(r"\([^()]*\)")


def _domain_of(address: str | None) -> str:
    """The domain half of an address, lowercased and bare."""
    _, _, domain = str(address or "").strip().lower().rpartition("@")
    return domain.strip().strip(">").strip()


def _aligned(candidate: str, from_domain: str) -> bool:
    """Is ``candidate`` the From domain, or a parent of it (relaxed alignment)?

    DMARC's relaxed mode aligns an organizational domain with its subdomains,
    so mail signed by ``example.com`` aligns with ``news.example.com``. Strict
    equality alone rejects a great deal of legitimate mail.
    """
    candidate = candidate.strip().strip(">").lower()
    if not candidate or not from_domain:
        return False
    return candidate == from_domain or from_domain.endswith(f".{candidate}")


def authentication_results_values(raw_headers: Any) -> list[str]:
    """Every ``Authentication-Results`` header, in the order it was written.

    Handles both shapes the providers use — a ``{name: value}`` dict and a
    ``[{name, value}]`` list — and only the list can carry more than one, which
    is why it is not collapsed to a map first. See the module docstring for why
    the order matters.
    """
    if isinstance(raw_headers, dict):
        found = [
            value
            for key, value in raw_headers.items()
            if str(key).strip().lower() == _HEADER
        ]
        return [str(value) for value in found if str(value or "").strip()]
    values: list[str] = []
    if isinstance(raw_headers, list):
        for header in raw_headers:
            if not isinstance(header, dict):
                continue
            if str(header.get("name") or "").strip().lower() != _HEADER:
                continue
            value = str(header.get("value") or "").strip()
            if value:
                values.append(value)
    return values


def _authserv_id(value: str) -> str:
    """The leading authserv-id — who is making these claims."""
    return _COMMENT.sub(" ", value).split(";")[0].strip().lower()


def _verdict_of(value: str, from_domain: str) -> EmailAuthenticationVerdict:
    """Read one header's clauses into a verdict."""
    # Comments are free text and may contain anything, including an address
    # that would otherwise be read as a property.
    cleaned = _COMMENT.sub(" ", value)
    methods = {
        name.lower(): result.lower() for name, result in _METHOD.findall(cleaned)
    }
    properties = {
        name.lower(): result.strip('"') for name, result in _PROPERTY.findall(cleaned)
    }

    # DMARC is the whole question in one clause: it passes only when an
    # authenticated identifier aligns with the From domain.
    dmarc = methods.get("dmarc")
    if dmarc == "pass":
        return EmailAuthenticationVerdict.PASS
    if dmarc in {"fail", "reject", "quarantine"}:
        return EmailAuthenticationVerdict.FAIL

    # No DMARC result. Alignment has to be established by hand, and an
    # unaligned pass is worth nothing: SPF passing for the bounce domain says
    # nothing about the From line a person reads.
    if methods.get("dkim") == "pass" and _aligned(
        properties.get("header.d", ""), from_domain
    ):
        return EmailAuthenticationVerdict.PASS
    if methods.get("spf") == "pass" and _aligned(
        _domain_of(properties.get("smtp.mailfrom")) or properties.get("smtp.helo", ""),
        from_domain,
    ):
        return EmailAuthenticationVerdict.PASS

    if methods.get("spf") in {"fail", "permerror"} or methods.get("dkim") == "fail":
        return EmailAuthenticationVerdict.FAIL
    return EmailAuthenticationVerdict.UNKNOWN


def evaluate_email_authentication(
    raw_headers: Any,
    *,
    from_address: str | None,
    trusted_authserv_ids: frozenset[str] | set[str] | None = None,
) -> EmailAuthenticationVerdict:
    """Was this ``From:`` line authenticated by the receiving mail service?

    ``trusted_authserv_ids`` names the receivers whose word is taken. When it is
    empty only the first header is read, because that is the one the receiving
    service prepended — a weaker guarantee, and the reason configuring the id is
    worth doing.
    """
    values = authentication_results_values(raw_headers)
    if not values:
        return EmailAuthenticationVerdict.UNKNOWN

    trusted = {str(item).strip().lower() for item in (trusted_authserv_ids or ())}
    if trusted:
        values = [value for value in values if _authserv_id(value) in trusted]
        if not values:
            # Headers exist but none from a receiver this deployment trusts,
            # which is the shape a forged header arrives in.
            return EmailAuthenticationVerdict.UNKNOWN
    else:
        values = values[:1]

    from_domain = _domain_of(from_address)
    verdicts = [_verdict_of(value, from_domain) for value in values]
    if EmailAuthenticationVerdict.FAIL in verdicts:
        return EmailAuthenticationVerdict.FAIL
    if EmailAuthenticationVerdict.PASS in verdicts:
        return EmailAuthenticationVerdict.PASS
    return EmailAuthenticationVerdict.UNKNOWN
