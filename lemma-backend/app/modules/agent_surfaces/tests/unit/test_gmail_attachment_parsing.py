"""Gmail attachment normalization edge cases: the many shapes a raw attachment
dict can arrive in (Composio's ``attachments``, a raw Gmail API MIME-part walk,
snake_case vs camelCase field names, and the ``body.*`` nesting Gmail uses for
the attachment id/size/inline data), and the de-duplication that keeps a file
mentioned in more than one of those collections from being attached twice.

None of this needs a live send round trip -- ``_normalize_attachment`` and
``_dedupe_attachments`` are pure functions, and the parser itself is a pure
transform from a payload dict to ``ParsedInboundSurfaceEvent``.
"""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.gmail.parser import (
    GmailMessageParser,
    _dedupe_attachments,
    _normalize_attachment,
)


def test_normalize_attachment_prefers_top_level_snake_case_fields():
    raw = {
        "attachment_id": "att-1",
        "name": "report.pdf",
        "mime_type": "application/pdf",
        "size": 2048,
        "content_bytes_base64": "cGRmYnl0ZXM=",
    }
    normalized = _normalize_attachment(raw, message_id="msg-2")
    assert normalized == {
        "id": "att-1",
        "name": "report.pdf",
        "mime_type": "application/pdf",
        "content_type": "application/pdf",
        "size": 2048,
        "message_id": "msg-2",
        "content_bytes_base64": "cGRmYnl0ZXM=",
    }


def test_normalize_attachment_falls_back_to_camelcase_and_body_fields():
    """A raw Gmail MIME part: no snake_case aliases, and both the attachment
    id and inline data live under ``body`` rather than at the top level."""
    raw = {
        "filename": "notes.txt",
        "mimeType": "text/plain",
        "body": {"attachmentId": "body-attachment-1", "size": 42, "data": "aGVsbG8="},
    }
    normalized = _normalize_attachment(raw, message_id="msg-1")
    assert normalized == {
        "id": "body-attachment-1",
        "name": "notes.txt",
        "mime_type": "text/plain",
        "content_type": "text/plain",
        "size": 42,
        "message_id": "msg-1",
        "content_bytes_base64": "aGVsbG8=",
    }


def test_normalize_attachment_top_level_id_wins_over_body_id():
    raw = {
        "attachmentId": "top-level-id",
        "filename": "notes.txt",
        "body": {"attachmentId": "should-be-ignored"},
    }
    normalized = _normalize_attachment(raw, message_id="msg-1")
    assert normalized is not None
    assert normalized["id"] == "top-level-id"


def test_normalize_attachment_empty_strings_fall_through_like_missing_values():
    """An empty string is falsy, so the alias chain skips it exactly as it
    would a missing key -- a blank ``mime_type`` does not "win" over a
    populated ``contentType``."""
    raw = {
        "attachment_id": "att-3",
        "name": "  ",
        "mime_type": "",
        "content_type": "application/pdf",
        "content_bytes_base64": "",
    }
    normalized = _normalize_attachment(raw, message_id="msg-3")
    assert normalized is not None
    assert normalized["id"] == "att-3"
    # A whitespace-only name is trimmed to empty and then normalized to None.
    assert normalized["name"] is None
    assert normalized["mime_type"] == "application/pdf"
    assert normalized["content_type"] == "application/pdf"
    assert normalized["content_bytes_base64"] is None


def test_normalize_attachment_returns_none_without_id_name_or_content():
    """Metadata alone (mime type, size) never identifies a real attachment --
    at least one of id/name/content must be present."""
    raw = {"mime_type": "image/png", "size": 10}
    assert _normalize_attachment(raw, message_id="msg-4") is None


def test_normalize_attachment_non_int_size_passes_through_unchanged():
    """Gmail can hand back ``size`` as a numeric string; the parser does not
    coerce it, only ``int`` values get normalized."""
    raw = {"attachment_id": "att-5", "size": "2048"}
    normalized = _normalize_attachment(raw, message_id="msg-5")
    assert normalized is not None
    assert normalized["size"] == "2048"


def test_dedupe_attachments_drops_exact_duplicates_keeps_distinct():
    items = [
        {
            "id": "att-1",
            "name": "report.pdf",
            "content_type": "application/pdf",
            "size": 10,
        },
        # Same (id, name, content_type) key as above -- dropped even though
        # other fields (size) differ.
        {
            "id": "att-1",
            "name": "report.pdf",
            "content_type": "application/pdf",
            "size": 999,
        },
        # Distinct content_type -> a different key, kept.
        {"id": "att-1", "name": "report.pdf", "content_type": "image/png"},
        {"id": None, "name": "unnamed", "content_type": None},
        # Duplicate (None, "unnamed", None) key -> dropped.
        {"id": None, "name": "unnamed", "content_type": None},
    ]
    deduped = _dedupe_attachments(items)
    assert len(deduped) == 3
    assert deduped[0]["size"] == 10  # first occurrence wins
    assert deduped[1]["content_type"] == "image/png"
    assert deduped[2]["name"] == "unnamed"


def test_gmail_message_parser_dedupes_attachment_seen_in_two_collections():
    """The same attachment can be described twice: once in Composio's
    top-level ``attachments`` list, and again inside a raw ``payload.parts``
    MIME walk (the same file, two shapes). Only one should reach the agent."""
    payload = {
        "data": {
            "thread_id": "thread-1",
            "message_id": "msg-1",
            "sender": "Customer <customer@example.com>",
            "to": "assistant@example.com",
            "subject": "Invoice",
            "message_text": "Please see attached invoice.",
            "attachments": [
                {
                    "attachment_id": "att-shared",
                    "name": "invoice.pdf",
                    "mime_type": "application/pdf",
                }
            ],
            "payload": {
                "parts": [
                    {
                        "filename": "invoice.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att-shared", "size": 4096},
                    },
                    {
                        "filename": "signature.png",
                        "mimeType": "image/png",
                        "body": {"attachmentId": "att-signature", "size": 128},
                    },
                ]
            },
        }
    }

    event = GmailMessageParser().parse(payload)

    assert event is not None
    attachments = event.metadata["attachments"]
    ids = [item["id"] for item in attachments]
    assert len(attachments) == 2
    assert ids.count("att-shared") == 1
    assert "att-signature" in ids


def test_gmail_message_parser_reads_attachment_list_alias():
    """``attachment_list`` is a second accepted key for the same collection
    (alongside ``attachments``) -- otherwise-identical payloads should not
    silently drop attachments delivered under this alias."""
    payload = {
        "data": {
            "thread_id": "thread-2",
            "message_id": "msg-2",
            "sender": "Customer <customer@example.com>",
            "to": "assistant@example.com",
            "subject": "Contract",
            "message_text": "Signed contract attached.",
            "attachment_list": [
                {
                    "attachment_id": "att-contract",
                    "name": "contract.pdf",
                    "mime_type": "application/pdf",
                }
            ],
        }
    }

    event = GmailMessageParser().parse(payload)

    assert event is not None
    attachments = event.metadata["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["id"] == "att-contract"
    assert attachments[0]["name"] == "contract.pdf"
