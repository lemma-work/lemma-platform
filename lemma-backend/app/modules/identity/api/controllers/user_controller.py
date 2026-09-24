from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.modules.identity.api.dependencies import UserServiceDep
from app.modules.identity.api.schemas.user_schemas import (
    InstallationResponse,
    UserProfileRequest,
    UserResponse,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.services.installation import get_signup_gate

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
    return UserResponse.model_validate(user_data)


@router.get(
    "/me/installation",
    operation_id="user.installation.get",
    summary="Get Installation",
    description=(
        "What kind of installation this is, whether the current user owns it, "
        "and who may sign up. On a Desktop installation the owner is the first "
        "account created, and is the only user granted host-level capabilities."
    ),
    response_model=InstallationResponse,
)
async def get_installation(request: Request) -> InstallationResponse:
    user: UserEntity = request.state.user
    view = await get_signup_gate().view_for(user.id)
    return InstallationResponse(
        deployment=view.deployment,
        is_owner=view.is_owner,
        signup_mode=view.signup_mode,
    )


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
    return UserResponse.model_validate(user_data)


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

    current_user_data.update_profile(**data.model_dump(exclude_unset=True))
    updated_user = await user_service.update_user(current_user_data)
    return UserResponse.model_validate(updated_user)
