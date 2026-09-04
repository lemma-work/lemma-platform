"""What the agent is told about files that arrived with a message.

The load-bearing case is a voice note: ingress already transcribed it into the
message text, so naming the audio file as something to go and read is an
invitation to transcribe it a second time.
"""

from __future__ import annotations

from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    _failed_files_block,
    _shared_files_blocks,
)


def _voice_metadata(*, failed: bool = False) -> dict:
    return {
        "ingested_files": ["/me/whatsapp/voice-1.ogg"],
        "voice_transcripts": [
            {
                "path": "/me/whatsapp/voice-1.ogg",
                "text": "" if failed else "book me a table for two",
                **({"failed": True} if failed else {"detected_language": "en"}),
            }
        ],
    }


def test_transcribed_voice_note_is_not_offered_as_a_file_to_read():
    blocks = _shared_files_blocks(_voice_metadata(), "WHATSAPP")
    joined = "\n\n".join(blocks)

    assert "already transcribed" in joined
    # Says the call is pointless rather than forbidden. It used to read "do NOT
    # call `listen`", which a model is free to ignore and sometimes did; the
    # tool now answers from the stored transcript, so this is a fact about what
    # would happen rather than a rule.
    assert "returns this same text" in joined
    # The path is still named — the audio stays available — but never under the
    # "shared files" framing that reads as "go and open this".
    assert "/me/whatsapp/voice-1.ogg" in joined
    assert "The user shared files" not in joined


def test_failed_transcription_leaves_the_audio_listed_for_listen():
    blocks = _shared_files_blocks(_voice_metadata(failed=True), "WHATSAPP")
    joined = "\n\n".join(blocks)

    # Nothing was transcribed, so `listen` is the right call and the file has
    # to be reachable.
    assert "The user shared files" in joined
    assert "/me/whatsapp/voice-1.ogg" in joined
    assert "returns this same text" not in joined


def test_other_attachments_survive_alongside_a_voice_note():
    metadata = _voice_metadata()
    metadata["ingested_files"] = ["/me/whatsapp/voice-1.ogg", "/me/whatsapp/menu.pdf"]

    blocks = _shared_files_blocks(metadata, "WHATSAPP")
    joined = "\n\n".join(blocks)

    assert "/me/whatsapp/menu.pdf" in joined
    assert "The user shared files" in joined
    # The pdf is listed as a shared file; the voice note is not.
    shared_block = next(b for b in blocks if b.startswith("The user shared files"))
    assert "voice-1.ogg" not in shared_block


def test_plain_files_are_unchanged_without_voice_metadata():
    blocks = _shared_files_blocks({"ingested_files": ["/me/slack/report.csv"]}, "SLACK")
    assert blocks == [
        "The user shared files; they are saved in the pod datastore at:\n"
        "- /me/slack/report.csv"
    ]


def test_a_file_that_never_arrived_is_named_rather_than_left_out():
    """Told nothing, the run answers the text alone and looks like it ignored
    the photo — which is what a failed download used to produce."""
    block = _failed_files_block(
        {
            "failed_files": [
                {"name": "image", "reason": "the download failed"},
                {"name": "clip.mov", "reason": "it is larger than the 50 MB limit"},
            ]
        }
    )

    assert block is not None
    assert "did NOT reach you" in block
    assert "image — the download failed" in block
    assert "clip.mov — it is larger than the 50 MB limit" in block


def test_no_failures_adds_no_block():
    assert _failed_files_block({}) is None
    assert _failed_files_block({"failed_files": []}) is None


def test_a_saved_file_and_a_lost_one_are_both_reported():
    """The saved-paths block returns as soon as it has any path, so the two
    cannot share one block: three photos of which one arrived is ordinary."""
    metadata = {
        "ingested_files": ["/me/whatsapp/image.jpg"],
        "failed_files": [{"name": "image", "reason": "the download failed"}],
    }

    joined = "\n\n".join(
        piece
        for piece in (
            *_shared_files_blocks(metadata, "WHATSAPP"),
            _failed_files_block(metadata),
        )
        if piece
    )

    assert "/me/whatsapp/image.jpg" in joined
    assert "did NOT reach you" in joined
