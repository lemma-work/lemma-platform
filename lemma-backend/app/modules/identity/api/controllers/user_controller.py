from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.core.api.dependencies import UoWDep
from app.core.app_pin import app_request_of
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.identity.api.dependencies import (
    AccountDeletionServiceDep,
    UserServiceDep,
)
from app.modules.identity.api.schemas.user_schemas import (
    AccountDeletionRequest,
    AccountDeletionResponse,
    UserProfileRequest,
    UserResponse,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.supertokens_auth.account_removal import (
    drop_cached_standing,
)

#: What a pod app's page is not told about the person viewing it (H20): it
#: learns who they are, not how to reach them off the platform or how old they
#: are. The person's own clients still read the whole profile.
_HIDDEN_FROM_APPS = {
    "mobile_number": None,
    "mobile_verified_at": None,
    "date_of_birth": None,
}


def _user_response(request: Request, user_data: UserEntity) -> UserResponse:
    response = UserResponse.model_validate(user_data)
    if app_request_of(request) is not None:
        return response.model_copy(update=_HIDDEN_FROM_APPS)
    return response


router = APIRouter(
    prefix="/users",
    tags=["Users"],
    redirect_slashes=False,
)


@router.get(
    "/me",
    operation_id="user.current.get",
    summary="Get Current User",
    description="Get the current authenticated user's information",
    response_model=UserResponse,
)
async def get_current_user(
    request: Request,
    user_service: UserServiceDep,
) -> UserResponse:
    """Get current user information."""
    user: UserEntity = request.state.user
    user_data = await user_service.get_user(user.id)
    return _user_response(request, user_data)


@router.get(
    "/me/profile",
    operation_id="user.profile.get",
    summary="Get User Profile",
    description="Get the current user's profile",
    response_model=UserResponse,
)
async def get_profile(
    request: Request,
    user_service: UserServiceDep,
) -> UserResponse:
    """Get user profile."""
    user: UserEntity = request.state.user
    user_data = await user_service.get_user(user.id)
    return _user_response(request, user_data)


@router.post(
    "/me/profile",
    status_code=status.HTTP_201_CREATED,
    operation_id="user.profile.upsert",
    summary="Create or Update Profile",
    description="Create or update the current user's profile",
    response_model=UserResponse,
)
async def upsert_profile(
    request: Request,
    data: UserProfileRequest,
    user_service: UserServiceDep,
) -> UserResponse:
    """Create or update user profile."""
    user: UserEntity = request.state.user
    current_user_data = await user_service.get_user(user.id)

    # `telegram_username` is still accepted so older clients are not refused,
    # and dropped here: a handle on a profile proves nothing.
    current_user_data.update_profile(
        **data.model_dump(exclude_unset=True, exclude={"telegram_username"})
    )
    updated_user = await user_service.update_user(current_user_data)
    return UserResponse.model_validate(updated_user)


@router.delete(
    "/me",
    operation_id="user.current.delete",
    summary="Delete Current User",
    description=(
        "Delete the signed-in person's account, for good. Their profile is "
        "erased, they leave every organization, and organizations nobody else "
        "is in are deleted with their pods. Work shared with others stays, "
        'shown as by "Deleted user". From the answer on, no token the person '
        "holds opens anything, this request's included; their sessions and "
        "login are removed within moments, after which the address can sign "
        "up again. A client should discard its tokens on success. A request "
        "for an account already deleted is refused 403 ACCOUNT_INACTIVE "
        "before it reaches here. "
        "Refused with 409 ACCOUNT_DELETION_SOLE_OWNER while the person is the "
        "only owner of an organization others are still in, and with 409 and "
        "the billing provider's code while something they pay for stands in "
        "the way."
    ),
    response_model=AccountDeletionResponse,
)
async def delete_current_user(
    request: Request,
    data: AccountDeletionRequest,
    deletion: AccountDeletionServiceDep,
    uow: UoWDep,
) -> AccountDeletionResponse:
    """Delete the current user's account."""
    user_id = request.state.user.id
    outcome = await deletion.delete_account(user_id, confirm_email=data.confirm_email)
    # Committed here rather than by the dependency, so the account's live
    # tokens are refused only once its deletion has happened -- and before
    # this answers, so nothing the person holds works by the time they read
    # it. Sessions and the login itself are removed by the work committed with
    # the deletion, never here: a failure after the commit must not answer 500
    # for an account that is already gone.
    await uow.commit()
    async with connection_released(uow.session):
        await drop_cached_standing(user_id)
    return AccountDeletionResponse(
        deleted=True,
        organizations_left=len(outcome.organizations_left),
        organizations_deleted=len(outcome.organizations_deleted),
        pods_deleted=outcome.pods_deleted,
    )
