"""What a shared page carries with it, decided without a pod or a request."""

from __future__ import annotations

import pytest

from app.modules.datastore.services.files.embedded_references import (
    embedded_references,
    may_travel_with,
    normalize_reference,
    page_kind,
    resolve_reference,
)
from app.modules.datastore.services.files.link_budget import OPEN_UNITS, open_units


class TestNormalizeReference:
    @pytest.mark.parametrize(
        "reference",
        [
            "https://example.com/a.png",
            "http://example.com/a.png",
            "//cdn.example.com/a.js",
            "data:image/png;base64,AAAA",
            "mailto:someone@example.com",
            "javascript:alert(1)",
            "#section",
            "",
            "   ",
        ],
    )
    def test_what_is_not_the_pods_is_left_to_the_browser(self, reference):
        assert normalize_reference(reference) is None

    def test_query_fragment_and_escapes_are_dropped(self):
        assert normalize_reference("Plan-files/My%20chart.png?v=2#x") == (
            "Plan-files/My chart.png"
        )

    def test_angle_brackets_and_entities_are_undone(self):
        assert normalize_reference("<Plan files/a.png>") == "Plan files/a.png"
        assert normalize_reference("a&amp;b.png") == "a&b.png"


class TestResolveReference:
    def test_relative_reads_from_the_pages_folder(self):
        assert (
            resolve_reference("Plan-files/chart.png", page_path="/pages/Plan.md")
            == "/pages/Plan-files/chart.png"
        )

    def test_parent_segments_climb_within_the_pod(self):
        assert (
            resolve_reference("../assets/logo.svg", page_path="/reports/q3/r.html")
            == "/reports/assets/logo.svg"
        )

    def test_absolute_is_the_pods_own_path(self):
        assert (
            resolve_reference("/me/chart.png", page_path="/pages/Plan.md")
            == "/me/chart.png"
        )

    def test_climbing_out_of_the_pod_names_nothing(self):
        assert resolve_reference("../../x.png", page_path="/pages/Plan.md") is None

    def test_a_page_at_the_root_reads_from_the_root(self):
        assert resolve_reference("a.png", page_path="/Plan.md") == "/a.png"


class TestEmbeddedReferences:
    def test_markdown_images_inline_reference_and_raw_html(self):
        page = (
            "# Report\n\n"
            "![chart](Plan-files/chart.png)\n"
            '![titled](<Plan files/b.png> "A title")\n'
            "![ref][logo]\n\n"
            "[logo]: assets/logo.png\n\n"
            '<img src="raw.png" alt="raw">\n'
            "![external](https://example.com/x.png)\n"
        )
        assert embedded_references(page, kind="markdown") == {
            "Plan-files/chart.png",
            "Plan files/b.png",
            "assets/logo.png",
            "raw.png",
        }

    def test_links_to_other_documents_do_not_travel(self):
        page = "See [the plan](plan.md) and <a href='budget.xlsx'>the budget</a>."
        assert embedded_references(page, kind="markdown") == frozenset()

    def test_html_loads_travel_and_its_links_do_not(self):
        page = """<!doctype html><html><head>
            <link rel="stylesheet" href="style.css">
            <link rel="icon" href="favicon.png">
            <link rel="canonical" href="other.html">
            <script src="chart.js"></script>
            <style>body { background: url('bg.jpg'); } @import "extra.css";</style>
          </head><body style="background-image: url(texture.png)">
            <img src="a.png" srcset="a-2x.png 2x, a-3x.png 3x">
            <video src="clip.mp4" poster="poster.jpg"></video>
            <picture><source srcset="wide.webp 1200w"></picture>
            <a href="next.html">Next</a>
            <img src="https://cdn.example.com/remote.png">
          </body></html>"""
        assert embedded_references(page, kind="html") == {
            "style.css",
            "favicon.png",
            "chart.js",
            "bg.jpg",
            "extra.css",
            "texture.png",
            "a.png",
            "a-2x.png",
            "a-3x.png",
            "clip.mp4",
            "poster.jpg",
            "wide.webp",
        }


class TestMayTravelWith:
    def test_a_personal_page_carries_whatever_its_owner_reads(self):
        for visibility in ("PERSONAL", "RESTRICTED", "POD", "PUBLIC"):
            assert may_travel_with(
                page_visibility="PERSONAL", resource_visibility=visibility
            )

    @pytest.mark.parametrize("page", ["POD", "RESTRICTED", "PUBLIC"])
    def test_a_page_others_can_edit_never_carries_a_private_file(self, page):
        assert not may_travel_with(page_visibility=page, resource_visibility="PERSONAL")
        assert not may_travel_with(
            page_visibility=page, resource_visibility="RESTRICTED"
        )
        assert may_travel_with(page_visibility=page, resource_visibility="POD")
        assert may_travel_with(page_visibility=page, resource_visibility="PUBLIC")


class TestPageKind:
    def test_markdown_by_type_or_by_name(self):
        assert page_kind("text/markdown", "x") == "markdown"
        assert page_kind("text/plain", "notes.md") == "markdown"

    def test_html_by_type_or_by_name(self):
        assert page_kind("text/html; charset=utf-8", "x") == "html"
        assert page_kind("application/octet-stream", "r.htm") == "html"

    def test_anything_else_carries_nothing(self):
        assert page_kind("application/pdf", "r.pdf") is None
        assert page_kind("image/png", "a.png") is None


class TestOpenUnits:
    """A live link counts opens of the file as it is now."""

    def test_a_whole_download_is_one_open(self):
        assert open_units(2048, 2048) == OPEN_UNITS

    def test_nothing_sent_costs_nothing(self):
        assert open_units(0, 2048) == 0

    def test_a_slice_costs_its_share(self):
        assert open_units(512, 2048) == OPEN_UNITS // 4

    def test_never_more_than_one_open(self):
        """A file that grew since its size was read still costs one open."""
        assert open_units(4096, 2048) == OPEN_UNITS

    def test_a_tiny_slice_of_a_large_file_is_not_free(self):
        assert open_units(1, 10**12) == 1

    def test_unknown_size_is_a_whole_open(self):
        assert open_units(10, 0) == OPEN_UNITS

    def test_slices_that_add_up_to_the_file_fit_one_open(self):
        assert sum(open_units(1, 3) for _ in range(3)) <= OPEN_UNITS
