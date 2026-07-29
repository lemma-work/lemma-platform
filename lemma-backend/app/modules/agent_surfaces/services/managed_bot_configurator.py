from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from PIL import Image

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfaceConfig
from app.modules.agent_surfaces.platforms.delivery import DeliveryClassification
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
    classify_telegram_error,
)
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    resolve_telegram_mini_app,
)

logger = get_logger(__name__)


async def configure_managed_bot(
    *,
    uow_factory,
    api_base_url: str | None,
    bot_token: str,
    pod_id: UUID,
    surface_name: str,
    pod_name: str,
    surface_config: dict[str, Any],
) -> None:
    child = TelegramClient(
        bot_token=bot_token,
        api_base=api_base_url,
        timeout=30,
    )
    config = SurfaceConfig.model_validate(surface_config)
    async with uow_factory() as uow:
        mini_app = await resolve_telegram_mini_app(
            uow=uow,
            pod_id=pod_id,
            app_name=config.telegram.app_name,
        )
    for method, payload in _profile_calls(
        surface_name=surface_name,
        pod_name=pod_name,
        mini_app=mini_app,
    ):
        try:
            await child.call(method, payload)
        except TelegramApiError as exc:
            if (
                classify_telegram_error(exc)
                is DeliveryClassification.TRANSIENT
            ):
                raise
            logger.debug(
                "agent_surfaces.telegram_manager.bot_branding_best_effort",
                method=method,
            )
    try:
        await child.call_multipart(
            "setMyProfilePhoto",
            fields={
                "photo": {
                    "type": "static",
                    "photo": "attach://profile_photo",
                }
            },
            files={
                "profile_photo": (
                    "lemma-agent.jpg",
                    _managed_bot_profile_photo(),
                    "image/jpeg",
                )
            },
        )
    except TelegramApiError as exc:
        if classify_telegram_error(exc) is DeliveryClassification.TRANSIENT:
            raise
        logger.debug("agent_surfaces.telegram_manager.bot_profile_photo_best_effort")
    except OSError:
        logger.debug("agent_surfaces.telegram_manager.bot_profile_photo_best_effort")


def _profile_calls(
    *,
    surface_name: str,
    pod_name: str,
    mini_app,
) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = [
        (
            "setMyDescription",
            {
                "description": (
                    f"{surface_name} connects Telegram to {pod_name} in Lemma. "
                    "Send a message, voice note, photo, or file to work with your agent."
                )[:512]
            },
        ),
        (
            "setMyShortDescription",
            {"short_description": f"Talk to {surface_name} for {pod_name}."[:120]},
        ),
        (
            "setMyCommands",
            {
                "commands": [
                    {"command": "help", "description": "See what this bot can do"},
                    {
                        "command": "retry",
                        "description": "Retry the last failed request",
                    },
                ]
            },
        ),
    ]
    menu_button: dict[str, Any] = {"type": "commands"}
    if mini_app and mini_app.url:
        menu_button = {
            "type": "web_app",
            "text": f"Open {mini_app.label}"[:64],
            "web_app": {"url": mini_app.url},
        }
    calls.append(("setChatMenuButton", {"menu_button": menu_button}))
    return calls


def _managed_bot_profile_photo() -> bytes:
    source_path = (
        Path(__file__).resolve().parents[4] / "public" / "icons" / "lemma.jpeg"
    )
    with Image.open(source_path) as source:
        logo = source.convert("RGB").crop((0, 0, min(source.width, 58), source.height))
        logo.thumbnail((300, 300), Image.Resampling.LANCZOS)
        avatar = Image.new("RGB", (512, 512), (247, 246, 241))
        avatar.paste(
            logo,
            ((avatar.width - logo.width) // 2, (avatar.height - logo.height) // 2),
        )
        output = BytesIO()
        avatar.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue()
