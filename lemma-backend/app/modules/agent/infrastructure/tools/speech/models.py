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
        description=(
            "A specific voice, by its full name — `aura-2-andromeda-en`, not "
            "`andromeda`. Omit it and one is chosen for `language`. Call "
            "`list_voices` to see what exists; there are far more than one per "
            "language, differing by accent, age and intended use."
        ),
    )


class SayResponse(BaseModel):
    success: bool = Field(default=False)
    message: str | None = None
    error: str | None = None
    audio_file_path: str | None = Field(
        default=None,
        description="Pod datastore path of the generated audio file.",
    )


class ListVoicesRequest(BaseModel):
    language: str | None = Field(
        default=None,
        description=(
            "BCP-47 code to filter by, e.g. 'es' or 'ja'. Omit to see every "
            "voice the provider has."
        ),
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="How many to return. There are roughly eighty in total.",
    )


class VoiceSummary(BaseModel):
    name: str = Field(description="Pass this to `say` as `voice`.")
    languages: list[str] = Field(default_factory=list)
    accent: str | None = None
    tags: list[str] = Field(default_factory=list)


class ListVoicesResponse(BaseModel):
    success: bool = Field(default=False)
    message: str | None = None
    error: str | None = None
    voices: list[VoiceSummary] = Field(default_factory=list)
    total: int = Field(
        default=0, description="How many matched before `limit` was applied."
    )
