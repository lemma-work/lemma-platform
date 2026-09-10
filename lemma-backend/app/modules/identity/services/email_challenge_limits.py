"""One email-send budget for browser and verified platform senders."""

from app.modules.identity.services.auth_abuse import AuthAbuseStore


async def enforce_challenge_send_limits(*, email: str, sender_key: str) -> None:
    store = AuthAbuseStore()
    email_digest = store.digest(email)
    sender_digest = store.digest(sender_key)
    for period, seconds, email_limit, sender_limit in (
        ("15m", 900, 3, 5),
        ("day", 86400, 6, 20),
    ):
        await store.enforce(
            f"identity:rate:email-action:email:{period}:{email_digest}",
            limit=email_limit,
            window_seconds=seconds,
            fail_closed=True,
        )
        await store.enforce(
            f"identity:rate:email-code:sender:{period}:{sender_digest}",
            limit=sender_limit,
            window_seconds=seconds,
            fail_closed=True,
        )
