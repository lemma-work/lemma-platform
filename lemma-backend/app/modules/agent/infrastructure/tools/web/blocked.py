"""Was this response a bot defence refusing us, or a page?

`web_fetch` could not tell, and the two failures that came of it are
opposites. A challenge page returning 200 with text in it was reported to the
agent as a successful fetch, and its text was written into the person's
workspace under the article's filename. A real article served under a
grumpy status was thrown away and re-fetched through a browser that cost
seven seconds and one of only five render slots.

**Neither the status nor the length can answer it.** Measured, all four on
real sites:

    reuters.com/technology/   429  with 6,935 characters of real article
    zillow.com                200  with a 106-character "Access denied"
    g2.com                    403  with 5,057 characters of real content
    nytimes.com block page    403  with 3,670 characters

So a length floor flags a genuine 117-character Stack Overflow answer and
misses a 3,670-character block page, and roughly a fifth of 403/429/503
responses in the literature are not blocks at all.

**Vendor headers can answer it**, and they are documented rather than
reverse-engineered. Two negatives matter as much as the positives, and both
were measured live while this was being written:

    nytimes.com   200  x-datadome: protected   + 1.38 MB of article
    any Cloudflare site       cf-ray present on every response

so `x-datadome` alone and `cf-ray` alone are not signals. What is a signal
is one of DataDome's *request-id* headers -- `x-dd-b` or `x-datadome-cid`,
either one -- arriving **together with a 403**; it is the status that does
the work, and the header only says whose refusal it is. For Cloudflare only
`cf-mitigated` counts. A naive regex for `captcha|cf-ray` matched 9 of 16
successfully-fetched pages.

Pure, stdlib-only and free of `except` on purpose: this is the piece that
has to be trivially testable against the fixtures above, and every rule that
fires is a decision about somebody's research.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from typing import Literal

Vendor = Literal["cloudflare", "datadome", "aws_waf", "perimeterx", "unnamed"]
Confidence = Literal["certain", "likely", "unknown"]

#: How much of a body the rules ever look at. Three times the largest block
#: page measured, and always enough to contain `<head>`.
BLOCK_SNIPPET_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class BlockVerdict:
    blocked: bool
    vendor: Vendor | None
    #: Which rule fired, for the log. Never shown to the agent.
    signal: str | None
    confidence: Confidence
    #: False when the evidence is about our address rather than our browser.
    #: A DataDome 403 refused curl, headed Chrome and headless Chrome
    #: identically; spending a render slot on it starves a page in the same
    #: batch that the browser would have rescued.
    browser_may_help: bool


NOT_BLOCKED = BlockVerdict(False, None, None, "unknown", True)
_STATUS_ONLY = BlockVerdict(False, None, "status_only", "unknown", True)


@dataclass(frozen=True, slots=True)
class _Evidence:
    status: int
    headers: Mapping[str, str]
    body: str
    title: str


#: Titles a block page actually uses, matched by equality after casefolding.
#: Equality and not substring: an article titled "CAPTCHA best practices" is
#: a page about the subject, and substring matching is what produced the
#: 9-in-16 false positive rate.
_BLOCK_TITLES: frozenset[str] = frozenset(
    {
        "just a moment...",
        "attention required! | cloudflare",
        "access to this page has been denied",
        "access to this page has been denied.",
        "pardon our interruption",
        "access denied",
        "you have been blocked",
        "verifying you are human",
        "bot verification",
        "are you a robot?",
        "security check",
    }
)

#: Paths that only a challenge page loads. Deliberately excludes bare
#: `captcha`, bare `cf-ray` and bare `cloudflare`, all of which appear on
#: pages that served perfectly well.
_BLOCK_MARKERS: tuple[tuple[str, Vendor], ...] = (
    ("/cdn-cgi/challenge-platform/", "cloudflare"),
    ("__cf_chl_", "cloudflare"),
    ("challenges.cloudflare.com/turnstile", "cloudflare"),
    ("geo.captcha-delivery.com", "datadome"),
    ("captcha-delivery.com", "datadome"),
    ("px-captcha", "perimeterx"),
    ("perimeterx.net", "perimeterx"),
    ("awswaf.com/captcha", "aws_waf"),
)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def title_of(body: str) -> str:
    """The `<title>`, flattened. Empty when there is not one."""
    found = _TITLE.search(body)
    if not found:
        return ""
    return " ".join(found.group(1).split())


def _cloudflare(evidence: _Evidence) -> BlockVerdict | None:
    """`cf-mitigated` is Cloudflare telling us it acted. `cf-ray` is not.

    Escalating to the browser is right here: a managed challenge is
    JavaScript, and the sandbox runs a real headed Chrome with a durable
    profile that can carry the clearance cookie afterwards.
    """
    mitigated = evidence.headers.get("cf-mitigated", "").strip().lower()
    if mitigated and mitigated != "none":
        return BlockVerdict(True, "cloudflare", "cf_mitigated", "certain", True)
    return None


def _datadome(evidence: _Evidence) -> BlockVerdict | None:
    """A request-id header *and* a 403, never a bare DataDome header.

    `x-datadome: protected` rides on responses that are serving normally --
    measured on nytimes.com, 200 with 1.38 MB of article -- so the vendor
    being present is not the signal. The 403 is; the header only attributes
    it.

    `or`, deliberately, between the two request-id headers. They are one
    fact in two spellings, not two conditions: g2.com was measured sending
    both, and nothing establishes that either always accompanies the other.
    Requiring both would turn a certain block into an unnamed one the moment
    DataDome dropped a header, and the cost of that is a browser render --
    one of five in a 240s budget -- spent on an address-shaped refusal the
    browser is refused by identically.
    """
    if evidence.status != 403:
        return None
    if "x-dd-b" in evidence.headers or "x-datadome-cid" in evidence.headers:
        return BlockVerdict(True, "datadome", "datadome_403", "certain", False)
    return None


def _aws_waf(evidence: _Evidence) -> BlockVerdict | None:
    """AWS documents the action header together with 405 or 202."""
    action = evidence.headers.get("x-amzn-waf-action", "").strip().lower()
    if action in ("captcha", "challenge") and evidence.status in (202, 405):
        return BlockVerdict(
            True, "aws_waf", f"aws_waf_{action}", "certain", action == "challenge"
        )
    return None


def _perimeterx(evidence: _Evidence) -> BlockVerdict | None:
    """PerimeterX's documented JSON block body.

    Only fires when the site chose JSON on its own. Asking for it -- adding
    `Accept: application/json` -- would double the cheap path's network cost
    and break the header coherence the impersonating client exists to keep,
    for a rule the HTML markers already cover.
    """
    content_type = evidence.headers.get("content-type", "")
    if "json" not in content_type.lower():
        return None
    if '"appId"' in evidence.body and "PX" in evidence.body:
        return BlockVerdict(True, "perimeterx", "px_json", "certain", False)
    if '"blockScript"' in evidence.body or '"jsClientSrc"' in evidence.body:
        return BlockVerdict(True, "perimeterx", "px_json", "certain", False)
    return None


def _title_marker(evidence: _Evidence) -> BlockVerdict | None:
    if evidence.title.strip().casefold() in _BLOCK_TITLES:
        return BlockVerdict(True, "unnamed", "block_title", "likely", True)
    return None


def _body_marker(evidence: _Evidence) -> BlockVerdict | None:
    lowered = evidence.body.lower()
    for marker, vendor in _BLOCK_MARKERS:
        if marker in lowered:
            return BlockVerdict(True, vendor, "block_marker", "likely", True)
    return None


#: Order is the policy. Certain header rules first, then markers. Status is
#: never consulted on its own.
_RULES: tuple[Callable[[_Evidence], BlockVerdict | None], ...] = (
    _cloudflare,
    _datadome,
    _aws_waf,
    _perimeterx,
    _title_marker,
    _body_marker,
)


def classify(
    *,
    status: int,
    headers: Mapping[str, str],
    body_snippet: str,
    title: str | None = None,
) -> BlockVerdict:
    """Whether a bot defence refused this request.

    `title` is optional because the browser path has one already and the
    HTTP path does not; when it is absent the `<title>` is read out of the
    snippet, so both callers get the same rules.
    """
    evidence = _Evidence(
        status=status,
        headers={key.lower(): value for key, value in headers.items()},
        body=body_snippet[:BLOCK_SNIPPET_BYTES],
        title=title if title is not None else title_of(body_snippet),
    )
    for rule in _RULES:
        verdict = rule(evidence)
        if verdict is not None:
            return verdict
    return _STATUS_ONLY


#: Statuses where a browser would get the same answer, so trying one is pure
#: cost. Not 403 or 429: both were measured serving real content.
_SETTLED_STATUSES = frozenset({404, 410})


def should_try_browser(verdict: BlockVerdict, *, status: int) -> bool:
    """Whether escalating to the browser could plausibly help.

    Unknown escalates, which is what keeps every case this detector has no
    opinion about behaving exactly as it did before.
    """
    if verdict.blocked:
        return verdict.browser_may_help
    return status not in _SETTLED_STATUSES


__all__ = [
    "BLOCK_SNIPPET_BYTES",
    "BlockVerdict",
    "NOT_BLOCKED",
    "classify",
    "should_try_browser",
    "title_of",
]
