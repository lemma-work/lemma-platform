"""Audit or apply required email verification to existing email/password users.

Dry-run is the default:
    uv run python scripts/reconcile_auth_users.py

Apply changes only after reviewing the JSON summary:
    uv run python scripts/reconcile_auth_users.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from supertokens_python.asyncio import get_users_oldest_first
from supertokens_python.recipe.emailverification.asyncio import is_email_verified
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user

from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.modules.identity.services.email_policy import (
    EmailPolicyError,
    validate_auth_email,
)


DEACTIVATION_REASONS = {
    "INVALID_SYNTAX",
    "INVALID_DOMAIN",
    "NULL_MX",
}


async def reconcile(*, apply: bool) -> dict[str, int]:
    initialize_supertokens()
    counts: Counter[str] = Counter()
    seen_user_ids: set[UUID] = set()
    pagination_token: str | None = None
    while True:
        page = await get_users_oldest_first(
            "public",
            limit=100,
            pagination_token=pagination_token,
            include_recipe_ids=["emailpassword"],
        )
        for supertokens_user in page.users:
            login_methods = [
                method
                for method in supertokens_user.login_methods
                if method.recipe_id == "emailpassword" and method.email
            ]
            for login_method in login_methods:
                email = login_method.email
                if email is None:
                    continue
                counts["audited"] += 1
                try:
                    user_id = UUID(login_method.recipe_user_id.get_as_string())
                except ValueError:
                    counts["invalid_user_id"] += 1
                    continue
                if user_id in seen_user_ids:
                    counts["duplicate_emailpassword_identity"] += 1
                    continue
                seen_user_ids.add(user_id)
                async with async_session_maker() as session:
                    local_user = await session.get(User, user_id)
                    if local_user is None:
                        counts["missing_local_user"] += 1
                        continue
                    try:
                        emails_match = normalize_identity_email(
                            local_user.email
                        ) == normalize_identity_email(email)
                    except ValueError:
                        emails_match = False
                    if not emails_match:
                        counts["email_conflict"] += 1
                        continue

                    rejection = None
                    try:
                        await validate_auth_email(email)
                    except EmailPolicyError as exc:
                        rejection = exc.rejection

                    if rejection and rejection.reason in DEACTIVATION_REASONS:
                        counts[f"deactivate_{rejection.reason.lower()}"] += 1
                        if apply:
                            local_user.is_active = False
                            local_user.deactivated_at = (
                                local_user.deactivated_at or datetime.now(timezone.utc)
                            )
                            local_user.deactivation_reason = rejection.reason
                            await session.commit()
                            await get_user_cache().invalidate(user_id)
                            await revoke_all_sessions_for_user(str(user_id))
                        continue

                    verified = await is_email_verified(
                        login_method.recipe_user_id,
                        email,
                    )
                    if verified:
                        counts["verified"] += 1
                        if apply and not local_user.is_verified:
                            # This is reconciliation of historical truth, not a new
                            # verification event, so it deliberately sends no welcome.
                            local_user.is_verified = True
                            local_user.email_verified_at = (
                                local_user.email_verified_at
                                or datetime.now(timezone.utc)
                            )
                            await session.commit()
                            await get_user_cache().invalidate(user_id)
                    else:
                        counts["verification_required"] += 1
                        if apply:
                            local_user.is_verified = False
                            local_user.email_verified_at = None
                            await session.commit()
                            await get_user_cache().invalidate(user_id)
                            await revoke_all_sessions_for_user(str(user_id))

        pagination_token = page.next_pagination_token
        if pagination_token is None:
            break
    counts["applied"] = int(apply)
    return dict(sorted(counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist state and revoke affected sessions (default is dry-run)",
    )
    args = parser.parse_args()
    print(
        json.dumps(asyncio.run(reconcile(apply=args.apply)), indent=2, sort_keys=True)
    )


if __name__ == "__main__":
    main()
