"""Message controls appear only on the final chunk and private setup destination."""

from pydantic import JsonValue, TypeAdapter
from app.modules.agent_surfaces.platforms.slack.blocks import markdown_block
from app.modules.agent_surfaces.platforms.slack.models import SLACK_RETRY_ACTION_ID

#: A failure notice's "Try again" control. The value is not a callback id -- a
#: retry re-runs whatever failed in the thread it was tapped in.
_RETRY_ACTIONS: dict[str, JsonValue] = {
    "type": "actions",
    "elements": [
        {
            "type": "button",
            "action_id": SLACK_RETRY_ACTION_ID,
            "text": {"type": "plain_text", "text": "Try again"},
            "value": "retry",
        }
    ],
}


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
    if (metadata or {}).get("retry_action") is True:
        blocks.append(_RETRY_ACTIONS)
    if is_dm:
        blocks.extend(
            TypeAdapter(list[dict[str, JsonValue]]).validate_python(
                (metadata or {}).get("onboarding_blocks") or []
            )
        )
    return blocks
