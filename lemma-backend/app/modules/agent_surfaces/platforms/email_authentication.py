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

**Why this parses rather than pattern-matches.** The header is written by the
receiver, but half of what it *contains* is the sender's: the envelope
``MAIL FROM`` is echoed into ``smtp.mailfrom=`` and, on most receivers, into a
comment beside it. Reading the whole header with one ``findall`` per pattern
handed that echoed text the same authority as the receiver's own verdict. Three
ways it went wrong, all of them reproduced against the previous version:

* Comments were stripped by a regex that knew nothing of nesting or quoting.
  ``(a (b) c)`` left ``(a  c)`` behind, and a ``)`` inside a quoted local part
  ended the comment early — either way, a ``dmarc=pass`` the sender wrote
  became a ``dmarc=pass`` the receiver appeared to have written.
* ``method=result`` pairs were collected into a dict, so a repeated method was
  last-wins: ``dmarc=fail; dmarc=pass`` read as a pass.
* Properties were collected across the whole header rather than per clause, so
  ``dkim=fail header.d=victim.com; dkim=pass`` paired the passing signature
  with the victim's domain.

So: comments come off with a scanner that tracks quote state and nesting depth,
the header is split into its RFC 8601 resinfo clauses, and each clause is read
with only its own properties. A quoted value is consumed whole, which is what
stops the sender's text from being scanned for verdicts at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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

# One ``key=value``. The value alternation puts the quoted form first so a
# quoted string is consumed *whole*: ``finditer`` resumes after it, so text the
# sender chose is never itself scanned for a ``method=result``. That is the
# property the previous ``findall`` lacked, and the reason a crafted
# ``smtp.mailfrom`` could smuggle a verdict.
_PAIR = re.compile(
    r'([A-Za-z][A-Za-z0-9_.-]*)\s*=\s*("(?:[^"\\]|\\.)*"|[^\s;]*)',
)

_METHODS = frozenset({"dmarc", "dkim", "spf"})
_DMARC_FAILURES = frozenset({"fail", "reject", "quarantine"})


def _strip_comments(value: str) -> str:
    """Remove RFC 5322 comments, honouring nesting and quoted strings.

    A comment may nest and may contain a quoted string, and the text inside one
    is frequently the sender's own address. Both facts have to be respected by
    the same pass, or removing comments becomes a way to inject content rather
    than a way to ignore it.

    An unbalanced quote inside a comment swallows the rest of the header, which
    reads as UNKNOWN rather than as a verdict. That is the safe direction: the
    caller's policy decides what to do with UNKNOWN, and nothing is believed.
    """
    out: list[str] = []
    depth = 0
    in_quotes = False
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            if depth == 0:
                out.append(char)
            continue
        if char == "\\":
            escaped = True
            if depth == 0:
                out.append(char)
            continue
        if in_quotes:
            if char == '"':
                in_quotes = False
            if depth == 0:
                out.append(char)
            continue
        if char == '"':
            in_quotes = True
            if depth == 0:
                out.append(char)
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            if depth:
                depth -= 1
                out.append(" ")
            continue
        if depth == 0:
            out.append(char)
    return "".join(out)


def _split_clauses(value: str) -> list[str]:
    """The header's ``;``-separated parts, ignoring separators inside quotes.

    The first part is the authserv-id; the rest are resinfo clauses. Splitting
    on a bare ``str.split(";")`` would break on a quoted value containing one.
    """
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            current.append(char)
            continue
        if char == "\\":
            escaped = True
            current.append(char)
            continue
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
            continue
        if char == ";" and not in_quotes:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


@dataclass(frozen=True, slots=True)
class _ResInfo:
    """One authentication method's result, with the properties that qualify it.

    Per clause, deliberately. ``header.d`` means "the domain *this* signature
    was made by", and letting it drift to a different clause's result is how a
    failing signature's domain came to vouch for a passing one.
    """

    method: str
    result: str
    properties: dict[str, str] = field(default_factory=dict)


def _pairs(clause: str) -> list[tuple[str, str]]:
    """Every ``key=value`` in one clause, with quoted values already unwrapped."""
    found: list[tuple[str, str]] = []
    for match in _PAIR.finditer(clause):
        key = match.group(1).strip().lower()
        raw = match.group(2).strip()
        if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            raw = raw[1:-1]
        found.append((key, raw))
    return found


def _resinfo(clause: str) -> _ResInfo | None:
    """Read one resinfo clause, or ``None`` when it states no method result."""
    method = ""
    result = ""
    properties: dict[str, str] = {}
    for key, value in _pairs(clause):
        if key in _METHODS and not method:
            method, result = key, value.lower()
            continue
        if "." in key:
            properties[key] = value
    if not method:
        return None
    return _ResInfo(method=method, result=result, properties=properties)


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


def _authserv_id(value: str) -> str:
    """The leading authserv-id — who is making these claims.

    RFC 8601 allows an optional version number after the id, so only the first
    token counts: ``mx.google.com 1;`` is still ``mx.google.com``.
    """
    head = _split_clauses(_strip_comments(value))[0].strip().lower()
    return head.split()[0] if head.split() else ""


def _clause_passes(info: _ResInfo, from_domain: str) -> bool:
    """Does this one clause authenticate the From domain by itself?

    An unaligned pass is worth nothing: SPF passing for the bounce domain says
    nothing about the From line a person reads, which is why alignment is
    established from the clause's *own* properties.
    """
    if info.result != "pass":
        return False
    if info.method == "dmarc":
        return True
    if info.method == "dkim":
        return _aligned(info.properties.get("header.d", ""), from_domain)
    envelope = _domain_of(info.properties.get("smtp.mailfrom")) or info.properties.get(
        "smtp.helo", ""
    )
    return _aligned(envelope, from_domain)


def _verdict_of(value: str, from_domain: str) -> EmailAuthenticationVerdict:
    """Read one header's clauses into a verdict."""
    clauses = _split_clauses(_strip_comments(value))[1:]
    infos = [info for info in (_resinfo(clause) for clause in clauses) if info]
    if not infos:
        return EmailAuthenticationVerdict.UNKNOWN

    # DMARC is evaluated once, so two answers to it means somebody wrote one of
    # them who should not have. Fail closed rather than picking a winner.
    dmarc = {info.result for info in infos if info.method == "dmarc"}
    if len({"pass"} & dmarc) and len(dmarc) > 1:
        return EmailAuthenticationVerdict.FAIL
    if dmarc & _DMARC_FAILURES:
        return EmailAuthenticationVerdict.FAIL

    if any(_clause_passes(info, from_domain) for info in infos):
        return EmailAuthenticationVerdict.PASS

    failed = any(
        (info.method == "spf" and info.result in {"fail", "permerror"})
        or (info.method == "dkim" and info.result == "fail")
        for info in infos
    )
    return (
        EmailAuthenticationVerdict.FAIL if failed else EmailAuthenticationVerdict.UNKNOWN
    )


def _expand(value: Any) -> list[str]:
    """One header field as the separate values it actually carries.

    Resend's Received Emails API returns a header *dict*, and a repeated header
    arrives there as a list — sometimes a real list, sometimes a JSON array
    inside a string, exactly as ``resend.inbound._header_value`` documents for
    ``References``. Stringifying that (``str([...])``) collapsed the receiver's
    header and a forged one into a single blob, where last-wins handed the
    forgery the verdict and the authserv-id parsed as ``"['mx.google.com"``.

    Kept as separate values rather than joined, because order is the whole
    defence: the receiver prepends its own, so the first is the one to read.
    """
    if isinstance(value, (list, tuple)):
        return [item for entry in value for item in _expand(entry)]
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            decoded = json.loads(text)
        except ValueError:
            return [text]
        if isinstance(decoded, list):
            return [item for entry in decoded for item in _expand(entry)]
    return [text]


def authentication_results_values(raw_headers: Any) -> list[str]:
    """Every ``Authentication-Results`` header, in the order it was written.

    Handles both shapes the providers use — a ``{name: value}`` dict and a
    ``[{name, value}]`` list. The dict is what Resend actually sends, and its
    values are not always strings; see :func:`_expand`.
    """
    if isinstance(raw_headers, dict):
        return [
            item
            for key, value in raw_headers.items()
            if str(key).strip().lower() == _HEADER
            for item in _expand(value)
        ]
    if isinstance(raw_headers, list):
        return [
            item
            for header in raw_headers
            if isinstance(header, dict)
            and str(header.get("name") or "").strip().lower() == _HEADER
            for item in _expand(header.get("value"))
        ]
    return []


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
    worth doing. Weaker in one specific way worth naming: a provider that adds
    no header of its own leaves the sender's forgery in first place. That is why
    :class:`SurfaceSettings` refuses the strict identity setting unless an
    authserv-id is configured alongside it.
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
