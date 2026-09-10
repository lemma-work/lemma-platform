"""SuperTokens supplies proof; Lemma owns completion and account selection."""

from __future__ import annotations

from dataclasses import dataclass, field

from supertokens_python.recipe.passwordless.asyncio import (
    check_code,
    create_code,
    revoke_code,
)
from supertokens_python.recipe.passwordless.interfaces import (
    APIInterface,
    CheckCodeOkResult,
)

from app.modules.identity.domain.email_challenge import CODE_TTL_SECONDS


def private_passwordless_apis(implementation: APIInterface) -> APIInterface:
    # The default endpoints bypass browser/platform binding and canonical-user
    # selection. Only Lemma's guarded challenge service may use this recipe.
    implementation.disable_create_code_post = True
    implementation.disable_resend_code_post = True
    implementation.disable_consume_code_post = True
    implementation.disable_email_exists_get = True
    implementation.disable_phone_number_exists_get = True
    return implementation


@dataclass(frozen=True, slots=True)
class ProviderEmailChallenge:
    pre_auth_session_id: str = field(repr=False)
    code_id: str = field(repr=False)
    device_id: str = field(repr=False)
    code: str = field(repr=False)
    expires_at_ms: int


async def issue_email_challenge(email: str) -> ProviderEmailChallenge:
    result = await create_code("public", email=email)
    return ProviderEmailChallenge(
        pre_auth_session_id=result.pre_auth_session_id,
        code_id=result.code_id,
        device_id=result.device_id,
        code=result.user_input_code,
        expires_at_ms=result.time_created
        + min(result.code_life_time, CODE_TTL_SECONDS * 1000),
    )


async def check_email_challenge(
    *, pre_auth_session_id: str, device_id: str, code: str
) -> bool:
    result = await check_code(
        "public",
        pre_auth_session_id=pre_auth_session_id,
        device_id=device_id,
        user_input_code=code,
    )
    return isinstance(result, CheckCodeOkResult)


async def revoke_email_challenge(code_id: str) -> None:
    await revoke_code("public", code_id=code_id)
