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
        description=(
            "Optional BCP-47 language code (e.g. 'en', 'hi'). Leave unset — the "
            "default reads mixed-language speech without being told. Set this "
            "only when you already know the language and the transcript came "
            "back wrong."
        ),
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
    language: str | None = Field(
        default=None,
        description=(
            "BCP-47 code for the language `text` is written in (e.g. 'es', "
            "'ja'). Pass it whenever you are not speaking English so the voice "
            "speaks that language rather than reading it with an English "
            "accent. None = the default voice."
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
