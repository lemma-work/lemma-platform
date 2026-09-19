"""Telling a bot defence from a page.

Every fixture here is a real response, captured through this repo's own
client while the rule set was being written. That matters more than usual,
because the plausible-looking version of each rule is wrong on a real site:
`x-datadome` rides on responses that are serving normally, `cf-ray` is on
every Cloudflare response there is, and a substring search for "captcha"
matched 9 of 16 pages that fetched perfectly well.

The two failures this is here to stop are opposites. A challenge page served
with a 200 used to be reported as a successful fetch, with its text written
into somebody's workspace under the article's filename. A real article served
under a grumpy status used to be thrown away and re-fetched through a browser
that costs seven seconds and one of only five render slots.
"""

from __future__ import annotations

import pytest

from app.modules.agent.tools.web.blocked import (
    classify,
    should_try_browser,
    title_of,
)

pytestmark = pytest.mark.unit


def _verdict(status: int, headers: dict[str, str], body: str = ""):
    return classify(status=status, headers=headers, body_snippet=body)


class TestTheCertainHeaderRules:
    def test_cloudflare_says_when_it_acted(self) -> None:
        """`cf-mitigated: challenge`, measured on a stackoverflow.com answer
        that returned 403 with 5,608 bytes titled "Just a moment..."."""
        verdict = _verdict(
            403,
            {"cf-mitigated": "challenge", "cf-ray": "9a1", "server": "cloudflare"},
            "<title>Just a moment...</title>",
        )

        assert verdict.blocked
        assert verdict.vendor == "cloudflare"
        assert verdict.confidence == "certain"
        assert should_try_browser(verdict, status=403), (
            "a managed challenge is JavaScript, and the sandbox runs a real "
            "headed Chrome that can carry the clearance cookie afterwards"
        )

    def test_a_header_name_in_any_case_is_still_the_header(self) -> None:
        assert _verdict(403, {"CF-Mitigated": "challenge"}).blocked

    def test_cf_mitigated_none_is_cloudflare_saying_it_did_nothing(self) -> None:
        assert not _verdict(200, {"cf-mitigated": "none"}).blocked

    def test_datadome_needs_the_pairing_not_the_bare_header(self) -> None:
        """The negative that matters most. nytimes.com answers 200 with
        `x-datadome: protected` and 1.38 MB of article; a rule that fired on
        that header alone would block the New York Times."""
        assert not _verdict(200, {"x-datadome": "protected"}).blocked

    def test_datadome_with_a_403_is_a_block(self) -> None:
        """g2.com, measured: 403, `x-dd-b: 259`, `x-datadome-cid`, 1,707
        bytes."""
        verdict = _verdict(403, {"x-dd-b": "259", "x-datadome-cid": "abc"})

        assert verdict.blocked
        assert verdict.vendor == "datadome"
        assert not should_try_browser(verdict, status=403), (
            "measured: the same 403 refused curl, headed Chrome and headless "
            "Chrome identically -- it is our address, not our browser"
        )

    def test_either_request_id_header_alone_is_enough(self) -> None:
        """Pinned because the `or` reads like a mistake and is not. The two
        headers are one fact in two spellings; the 403 is the condition.
        Requiring both would downgrade a certain DataDome block to an
        unnamed one the moment a response carried only one of them, and an
        unnamed 403 is escalated to a browser that gets refused identically.
        """
        for header in ("x-dd-b", "x-datadome-cid"):
            verdict = _verdict(403, {header: "259"})
            assert verdict.blocked, header
            assert verdict.vendor == "datadome", header
            assert not verdict.browser_may_help, header

    def test_a_request_id_header_without_a_403_is_not_a_block(self) -> None:
        """The other half of the same rule: the status carries it."""
        assert not _verdict(200, {"x-dd-b": "259"}).blocked

    def test_datadome_wins_when_a_site_sits_behind_both(self) -> None:
        """g2.com really does send `x-dd-b` and `cf-ray` together, and the
        two rules disagree about whether a browser would help -- so which
        one runs first is a decision, not an ordering detail."""
        verdict = _verdict(
            403,
            {
                "x-dd-b": "259",
                "x-datadome-cid": "abc",
                "cf-ray": "9a1",
                "server": "cloudflare",
            },
        )

        assert verdict.vendor == "datadome"
        assert not should_try_browser(verdict, status=403)

    @pytest.mark.parametrize("status", [202, 405])
    def test_aws_waf_on_a_documented_status(self, status: int) -> None:
        verdict = _verdict(status, {"x-amzn-waf-action": "captcha"})

        assert verdict.blocked
        assert verdict.vendor == "aws_waf"

    def test_aws_waf_header_on_an_ordinary_status_is_not_a_block(self) -> None:
        """AWS documents the pairing. Without it this is a page that merely
        passed through a WAF, which is most pages behind one."""
        assert not _verdict(200, {"x-amzn-waf-action": "captcha"}).blocked

    def test_perimeterx_json_block_body(self) -> None:
        verdict = _verdict(
            403,
            {"content-type": "application/json"},
            '{"appId":"PXabc","jsClientSrc":"/x","blockScript":"/y"}',
        )

        assert verdict.blocked
        assert verdict.vendor == "perimeterx"


class TestTheThingsThatLookLikeSignalsAndAreNot:
    def test_cf_ray_alone_is_on_every_cloudflare_response(self) -> None:
        assert not _verdict(
            200, {"cf-ray": "9a1", "server": "cloudflare"}, "<title>An article</title>"
        ).blocked

    def test_an_article_about_captchas_is_an_article(self) -> None:
        """The substring trap, and the reason titles match by equality.
        A regex for `captcha|cf-ray` matched 9 of 16 pages that were fine."""
        verdict = _verdict(
            200,
            {},
            "<title>CAPTCHA best practices</title>"
            + " captcha " * 40
            + "cf-ray is a header",
        )

        assert not verdict.blocked

    def test_a_status_on_its_own_is_never_a_block(self) -> None:
        """Roughly a fifth of 403/429/503 responses are not bot blocks."""
        for status in (403, 429, 503):
            verdict = _verdict(status, {}, "<title>Something went wrong</title>")
            assert not verdict.blocked
            assert verdict.signal == "status_only"


class TestTheCasesThatDroveThis:
    def test_a_429_carrying_the_whole_article_is_not_a_block(self) -> None:
        """reuters.com/technology/, measured: 429 with 6,935 characters of
        real article. Treating the status as the answer threw that away."""
        verdict = _verdict(429, {"server": "openresty"}, "<title>Tech news</title>")

        assert not verdict.blocked
        assert should_try_browser(verdict, status=429)

    def test_a_200_challenge_page_is_a_block(self) -> None:
        """zillow.com, measured: 200 with a 106-character "Access to this
        page has been denied". Reported to the agent as a successful fetch,
        and written into the workspace under the article's name."""
        verdict = _verdict(
            200, {}, "<title>Access to this page has been denied</title>"
        )

        assert verdict.blocked
        assert verdict.confidence == "likely"

    def test_a_long_block_page_is_a_block(self) -> None:
        """3,670 characters -- comfortably past any length floor, which is
        why length cannot be the signal."""
        verdict = _verdict(
            403, {}, "<title>Pardon Our Interruption</title>" + "x" * 3600
        )

        assert verdict.blocked

    def test_a_short_real_page_is_not(self) -> None:
        """117 body characters, a genuine Stack Overflow answer -- the
        length floor's false positive, and not this rule's problem."""
        verdict = _verdict(200, {"cf-ray": "9a1"}, "<title>How do I do X?</title>short")

        assert not verdict.blocked


class TestWhetherToSpendABrowser:
    def test_a_settled_status_does_not_get_a_render(self) -> None:
        """404 and 410 mean the same to a browser, and a render is one of
        only five slots in the batch."""
        verdict = _verdict(404, {})

        assert not should_try_browser(verdict, status=404)

    def test_anything_unrecognised_still_escalates(self) -> None:
        """The default that keeps every case this detector has no opinion
        about behaving exactly as it did before it existed."""
        verdict = _verdict(500, {}, "<title>Server error</title>")

        assert should_try_browser(verdict, status=500)


class TestReadingTheTitle:
    def test_a_title_across_lines_is_flattened(self) -> None:
        assert title_of("<title>\n  Just a moment...\n</title>") == "Just a moment..."

    def test_no_title_is_empty_not_an_error(self) -> None:
        assert title_of("<body>no head at all</body>") == ""

    def test_a_title_with_attributes_still_reads(self) -> None:
        assert title_of('<title data-x="1">Access denied</title>') == "Access denied"

    def test_a_supplied_title_is_used_instead_of_the_body(self) -> None:
        """The browser path has a title already, extracted from markdown,
        and no HTML left to read one out of."""
        verdict = classify(
            status=0, headers={}, body_snippet="", title="Just a moment..."
        )

        assert verdict.blocked
