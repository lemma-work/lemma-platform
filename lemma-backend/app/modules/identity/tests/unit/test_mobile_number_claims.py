from unittest.mock import AsyncMock

import pytest

from app.modules.identity.infrastructure.mobile_number_claims import (
    acquire_mobile_number_claim_lock,
)


@pytest.mark.asyncio
async def test_mobile_claim_lock_is_stable_and_does_not_expose_phone_digits():
    first_session = AsyncMock()
    second_session = AsyncMock()

    await acquire_mobile_number_claim_lock(first_session, "14155552671")
    await acquire_mobile_number_claim_lock(second_session, "14155552671")

    first_statement, first_parameters = first_session.execute.await_args.args
    second_statement, second_parameters = second_session.execute.await_args.args
    assert str(first_statement) == "SELECT pg_advisory_xact_lock(:lock_key)"
    assert str(second_statement) == str(first_statement)
    assert first_parameters == second_parameters
    assert isinstance(first_parameters["lock_key"], int)
    assert "14155552671" not in str(first_parameters)


@pytest.mark.asyncio
@pytest.mark.parametrize("digits", ["", "+14155552671", "not-a-number"])
async def test_mobile_claim_lock_requires_normalized_digits(digits: str):
    session = AsyncMock()

    with pytest.raises(ValueError, match="Normalized mobile digits are required"):
        await acquire_mobile_number_claim_lock(session, digits)

    session.execute.assert_not_awaited()
