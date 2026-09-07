from typing import AsyncGenerator, Annotated
from fastapi import Depends
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    create_uow_from_session_maker,
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)


from app.core.domain.entity import AuthenticatedPrincipal
from fastapi import Request, HTTPException, status


async def get_uow() -> AsyncGenerator[SqlAlchemyUnitOfWork, None]:
    """Dependency that provides a Unit of Work instance.

    This handles the session lifecycle: creates a session, yields the UoW,
    and handles commit/rollback automatically via the context manager.
    """
    async with create_uow_from_session_maker(async_session_maker) as uow:
        yield uow


def get_uow_factory() -> UnitOfWorkFactory:
    """Dependency that provides a Unit of Work factory."""
    return SessionUnitOfWorkFactory(async_session_maker)


def get_current_user(request: Request) -> AuthenticatedPrincipal:
    """The principal the auth middleware put on the request.

    Annotated as what it actually returns. It used to say `UserEntity`, an
    `AggregateRoot` with an email and a superuser flag -- but `app/core/security.py`
    is the only thing that assigns `request.state.user` and it assigns an
    `AuthenticatedPrincipal`, so any caller trusting that annotation for
    anything past `.id` would have got an `AttributeError` at runtime.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated",
        )
    return user


#: ``scope="function"`` is load-bearing, not a tidy-up.
#:
#: The commit lives in ``get_uow``'s teardown. FastAPI's default dependency
#: scope is ``"request"``, whose teardown runs *after* the response has been
#: sent — so on the default a client receives ``201 Created`` and only then does
#: the transaction commit. Two consequences, both real:
#:
#: * A client that uses what it just created can be refused, because the write
#:   is not visible yet. Create an organization, then create a pod in it, and
#:   the pod is refused for a membership row that is still uncommitted.
#: * A commit that *fails* fails after the client has been told it succeeded.
#:
#: ``"function"`` ends the dependency after the path operation and before the
#: response goes out, which is what a success response has to mean. Response
#: serialization already happens inside the endpoint call, and no streaming
#: endpoint holds this session while its body streams — those take
#: ``get_uow_factory`` and open their own — so nothing needs the session to
#: outlive the handler.
UoWDep = Annotated[SqlAlchemyUnitOfWork, Depends(get_uow, scope="function")]
CurrentUser = Annotated[AuthenticatedPrincipal, Depends(get_current_user)]
