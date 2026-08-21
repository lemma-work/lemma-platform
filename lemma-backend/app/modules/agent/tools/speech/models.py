from __future__ import annotations

from pydantic import BaseModel, Field


class ListenRequest(BaseModel):
    """Transcribe a pod/workspace audio file to text."""

    file_path: str = Field(
        description=(
            "Path to the audio file to transcribe. Accepts a pod datastore path "
            "(e.g. /me/telegram/voice.ogg) or a workspace path."
        )
    )
    language: str | None = Field(
        default=None,
        description="Optional BCP-47 language hint (e.g. 'en'). None = auto-detect.",
    )


class ListenResponse(BaseModel):
    success: bool = Field(default=False)
    message: str | None = None
    error: str | None = None
    transcript: str | None = Field(default=None, description="The transcribed text.")
    detected_language: str | None = None
    duration_seconds: float | None = None


class SayRequest(BaseModel):
    """Generate spoken audio (MP3) from text."""

    text: str = Field(description="The text to speak.")
    output_file_path: str | None = Field(
        default=None,
        description=(
            "Optional pod datastore path for the generated .mp3 (e.g. "
            "/me/speech/reply.mp3). Defaults to a generated /me/speech/<id>.mp3."
        ),
    )
    voice: str | None = Field(
        default=None,
        description="Optional provider-specific voice/model id. None = default.",
    )


class SayResponse(BaseModel):
    success: bool = Field(default=False)
    message: str | None = None
    error: str | None = None
    audio_file_path: str | None = Field(
        default=None,
        description="Pod datastore path of the generated audio file.",
    )
