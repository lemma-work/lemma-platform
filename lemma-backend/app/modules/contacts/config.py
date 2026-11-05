"""Contacts module configuration: how handles are spelled, and how often a
contact may be written to."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class ContactsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    contacts_default_phone_region: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "The ISO 3166 region (``GB``, ``US``) a phone number written without "
            "its country code is read in, until pods carry a region of their "
            "own. Unset, such a number is read as international: every platform "
            "that vouches for a phone number (WhatsApp's ``wa_id``) sends it "
            "with its country code and without the ``+``."
        ),
    )
    contacts_member_lines_per_contact_per_day: int = Field(
        default=10,
        ge=0,
        description=(
            "How many lines members may write to one contact in a day. Each one "
            "speaks for the pod to somebody who did not ask, on a number or "
            "address whose sender reputation the pod depends on. Counted once "
            "per line, however often its delivery is retried."
        ),
    )


contacts_settings = ContactsSettings()
