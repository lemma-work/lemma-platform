"""Message controls appear only on the final chunk and private setup destination."""

from pydantic import JsonValue, TypeAdapter
from app.modules.agent_surfaces.platforms.slack.blocks import markdown_block


def reply_blocks(
    chunk: str,
    metadata: dict[str, object] | None,
    *,
    is_dm: bool,
    is_last: bool,
) -> list[dict[str, JsonValue]]:
    blocks = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
        [markdown_block(chunk)]
    )
    if not is_last:
        return blocks
    if is_dm:
        blocks.extend(
            TypeAdapter(list[dict[str, JsonValue]]).validate_python(
                (metadata or {}).get("onboarding_blocks") or []
            )
        )
    return blocks
