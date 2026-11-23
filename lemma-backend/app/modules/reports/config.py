"""Reports module configuration: where a report is sent."""

from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class ReportsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    reports_email: EmailStr | None = Field(
        default=None,
        description=(
            "Where each report a person files is emailed: the inbox of whoever "
            "runs this deployment and answers for what is said in it. The email "
            "carries the report and a copy of what was reported, with Reply-To "
            "set to the person who reported it. Unset, reports are kept and "
            "nothing is sent."
        ),
    )


reports_settings = ReportsSettings()
