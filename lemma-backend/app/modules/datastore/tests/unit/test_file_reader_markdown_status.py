"""An absent derived markdown explains itself from the file's own status.

`get_document_markdown` used to raise the same 404 whether the file was still
in the processing queue or would never have markdown at all. During the tool
sweep that produced exactly one message -- "Converted markdown for … not found"
-- for a PDF that was simply still PENDING, which reads as "this document is
unreadable" rather than "try again shortly".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.services.files.reader import _missing_markdown_message


def _file(status: FileStatus):
    return SimpleNamespace(status=status, path="/me/toolcheck/toolcheck.pdf")


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (FileStatus.PENDING, "has not finished processing yet"),
        (FileStatus.PROCESSING, "has not finished processing yet"),
        (FileStatus.FAILED, "could not be converted"),
        (FileStatus.FAILED_PERMANENT, "could not be converted"),
        (FileStatus.NOT_REQUIRED, "not an indexable document"),
    ],
)
def test_the_message_names_why_there_is_no_markdown(status, needle):
    message = _missing_markdown_message(_file(status))
    assert needle in message
    assert "/me/toolcheck/toolcheck.pdf" in message


def test_a_completed_file_with_no_markdown_keeps_the_plain_message():
    """COMPLETED with no markdown is genuinely "not found" and nothing more --
    inventing a reason for it would be worse than the original message."""
    message = _missing_markdown_message(_file(FileStatus.COMPLETED))
    assert message == "Converted markdown for /me/toolcheck/toolcheck.pdf not found"


def test_pending_is_reported_as_retryable_not_as_missing():
    """The distinction that matters to a caller: wait, or give up."""
    pending = _missing_markdown_message(_file(FileStatus.PENDING))
    permanent = _missing_markdown_message(_file(FileStatus.FAILED_PERMANENT))
    assert "Retry" in pending
    assert "Retry" not in permanent
