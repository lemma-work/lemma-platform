"""`pod_edit_file` places every edit or none, and says which one it could not."""

from app.modules.agent.services.attached_document_brief import (
    attached_file_path,
    render_attached_document,
)
from app.modules.agent.tools.pod.models import FileEdit
from app.modules.agent.tools.pod.pod_file_tools import _apply_edits
from types import SimpleNamespace

DOC = "# Title\n\nIntro line.\n\n⟦Scout is writing: two lines⟧\n\nOutro.\n"


class TestApplyEdits:
    def test_a_marker_line_is_replaced_and_the_rest_kept(self):
        result = _apply_edits(
            DOC,
            [FileEdit(old_text="⟦Scout is writing: two lines⟧", new_text="One.\nTwo.")],
        )

        assert result == (DOC.replace("⟦Scout is writing: two lines⟧", "One.\nTwo."), 1)

    def test_edits_apply_in_order_each_seeing_the_last(self):
        result = _apply_edits(
            DOC,
            [
                FileEdit(old_text="Intro line.", new_text="Intro, tightened."),
                FileEdit(old_text="Intro, tightened.", new_text="Intro, final."),
            ],
        )

        assert isinstance(result, tuple)
        assert "Intro, final." in result[0]

    def test_text_that_is_not_there_changes_nothing_and_names_the_edit(self):
        result = _apply_edits(
            DOC,
            [
                FileEdit(old_text="Intro line.", new_text="changed"),
                FileEdit(old_text="not in the doc", new_text="x"),
            ],
        )

        assert isinstance(result, str)
        assert result.startswith("Edit 2:")

    def test_an_ambiguous_match_is_refused_unless_all_are_meant(self):
        text = "a\nx\na\n"

        refused = _apply_edits(text, [FileEdit(old_text="a", new_text="b")])
        everywhere = _apply_edits(
            text, [FileEdit(old_text="a", new_text="b", replace_all=True)]
        )

        assert isinstance(refused, str) and "2 times" in refused
        assert everywhere == ("b\nx\nb\n", 2)


class TestAttachedDocument:
    def test_the_path_is_read_from_conversation_metadata(self):
        conversation = SimpleNamespace(
            metadata={"lemma_attached_file": "/Pages/Plan.md"}
        )

        assert attached_file_path(conversation) == "/Pages/Plan.md"

    def test_no_path_or_a_relative_one_means_no_section(self):
        assert attached_file_path(SimpleNamespace(metadata={})) is None
        assert (
            attached_file_path(
                SimpleNamespace(metadata={"lemma_attached_file": "plan.md"})
            )
            is None
        )
        assert attached_file_path(SimpleNamespace(metadata=None)) is None

    def test_a_short_doc_is_shown_whole(self):
        section = render_attached_document("/doc.md", DOC, limit=1000)

        assert DOC in section
        assert "Only the first" not in section

    def test_a_long_doc_is_cut_and_says_so(self):
        section = render_attached_document("/doc.md", "x" * 50, limit=10)

        assert "x" * 10 in section and "x" * 11 not in section
        assert "Only the first 10 of 50 characters" in section
