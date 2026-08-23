"""What the agent is told about files that arrived with a message.

The load-bearing case is a voice note: ingress already transcribed it into the
message text, so naming the audio file as something to go and read is an
invitation to transcribe it a second time.
"""

from __future__ import annotations

from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
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
    assert "do NOT call `listen`" in joined
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
    assert "do NOT call `listen`" not in joined


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
