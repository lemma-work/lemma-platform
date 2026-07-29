from __future__ import annotations

from typing import Any


def next_telegram_offset(
    updates: list[dict[str, Any]],
    current: int | None,
) -> int | None:
    ids = [
        update_id
        for update in updates
        for update_id in [update.get("update_id")]
        if isinstance(update_id, int)
    ]
    return max(ids) + 1 if ids else current


def assemble_telegram_updates(
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assembled: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        if len(pending) == 1:
            assembled.append(pending[0])
            pending = []
            return
        first = pending[0]
        assembled.append(
            {
                **first,
                "update_id": max(int(item.get("update_id") or 0) for item in pending),
                "_lemma_batch_messages": [
                    item["message"]
                    for item in pending
                    if isinstance(item.get("message"), dict)
                ],
            }
        )
        pending = []

    for update in updates:
        message = update.get("message")
        if not isinstance(message, dict):
            flush()
            assembled.append(update)
            continue
        if pending and not telegram_messages_share_burst(
            pending[-1]["message"],
            message,
        ):
            flush()
        pending.append(update)
    flush()
    return assembled


def telegram_messages_share_burst(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    if (
        (previous.get("chat") or {}).get("id") != (current.get("chat") or {}).get("id")
        or (previous.get("from") or {}).get("id")
        != (current.get("from") or {}).get("id")
        or previous.get("message_thread_id") != current.get("message_thread_id")
    ):
        return False
    previous_group = previous.get("media_group_id")
    current_group = current.get("media_group_id")
    if previous_group or current_group:
        return bool(previous_group and previous_group == current_group)
    previous_date = previous.get("date")
    current_date = current.get("date")
    try:
        if previous_date is None or current_date is None:
            return True
        return abs(int(current_date) - int(previous_date)) <= 2
    except TypeError, ValueError:
        return True
