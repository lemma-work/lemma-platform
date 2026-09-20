"""Administer the WhatsApp number pool on a running deployment.

The pool is deployment inventory: rows saying which numbers this installation
owns and which of them may still be handed to an organisation. They have no pod and
no organisation, so there is nobody who could be authorised to edit them
through the API -- "the deployment operator" is not a principal this platform
has -- and inventing a super-admin to expose four writes would be a much larger
thing than the four writes. So this is a script, run where the database is
reachable, exactly as ``import_connector_catalog.py`` is.

Secrets are read from named environment variables and never from flags: a flag
lands in shell history and in every process listing on the host for as long as
the command runs.

Usage::

    # What the deployment owns
    uv run python scripts/whatsapp_numbers.py list

    # Add a number to the pool (secrets from the environment)
    WHATSAPP_NUMBER_ACCESS_TOKEN=... \\
    WHATSAPP_NUMBER_APP_SECRET=... \\
    WHATSAPP_NUMBER_VERIFY_TOKEN=... \\
    uv run python scripts/whatsapp_numbers.py add \\
        --phone-number-id 123456789012345 \\
        --display-phone-number +15551234567 \\
        --waba-id 987654321098765

    # Stop handing a number out, without disturbing whoever holds it
    uv run python scripts/whatsapp_numbers.py retire --phone-number-id 1234...

    # Drop a number the deployment no longer owns
    uv run python scripts/whatsapp_numbers.py remove --phone-number-id 1234...

Any credential left unset is stored as NULL, which means "fall back to
``surface_settings.whatsapp_*``" -- the state a one-number deployment is in.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
)
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)

#: Each secret's own variable, named for the column it fills. One blob of JSON
#: would be shorter and would put three secrets in one place a shell can echo.
_ACCESS_TOKEN_VAR = "WHATSAPP_NUMBER_ACCESS_TOKEN"
_APP_SECRET_VAR = "WHATSAPP_NUMBER_APP_SECRET"
_VERIFY_TOKEN_VAR = "WHATSAPP_NUMBER_VERIFY_TOKEN"


def _from_environment(name: str) -> str | None:
    """A secret, or None when the operator did not set one.

    Empty is None rather than an empty secret: an unset variable and one set to
    nothing are the same intention, and an empty string stored in the column
    would read as "this number declares its own token" while matching nothing.
    """
    return (os.environ.get(name) or "").strip() or None


async def _list() -> int:
    async with async_session_maker() as session:
        numbers = await WhatsAppNumberRepository(
            SqlAlchemyUnitOfWork(session)
        ).list_all()
    if not numbers:
        print("No numbers in the pool; the deployment uses surface_settings only.")
        return 0
    for number in numbers:
        # Which credentials the row carries, not what they are.
        #
        # `bool(...)` at the point the tuple is built rather than a truthiness
        # test on the secret itself further down. The two are the same answer,
        # but only one of them keeps the secret out of the expression that
        # reaches `print` -- and CodeQL read the other as
        # `py/clear-text-logging-sensitive-data`, correctly in the sense that it
        # could not see the value was never printed. A shape the analyser can
        # read beats an allowlist entry that has to be re-argued on every scan.
        declared = ",".join(
            name
            for name, carries in (
                ("access_token", bool(number.access_token)),
                ("app_secret", bool(number.app_secret)),
                ("verify_token", bool(number.verify_token)),
            )
            if carries
        )
        print(
            f"{number.phone_number_id}\t{number.display_phone_number}\t"
            f"{number.waba_id}\t{number.status.value}\t"
            f"own:[{declared}]\t{number.notes or ''}"
        )
    return 0


async def _add(arguments: argparse.Namespace) -> int:
    entity = WhatsAppNumberEntity(
        phone_number_id=arguments.phone_number_id,
        display_phone_number=arguments.display_phone_number,
        waba_id=arguments.waba_id,
        access_token=_from_environment(_ACCESS_TOKEN_VAR),
        app_secret=_from_environment(_APP_SECRET_VAR),
        verify_token=_from_environment(_VERIFY_TOKEN_VAR),
        onboarding_email_flow_id=arguments.onboarding_email_flow_id,
        onboarding_code_flow_id=arguments.onboarding_code_flow_id,
        notes=arguments.notes,
    )
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await WhatsAppNumberRepository(uow).create(entity)
        await uow.commit()
    print(f"Added {entity.display_phone_number} ({entity.phone_number_id}).")
    # The webhook override is Meta's to hold, not this table's, and it is set
    # with the same `verify_token` this row now carries.
    print(
        "Point Meta at this number's own callback with "
        f"POST /{entity.phone_number_id} and a webhook_configuration whose "
        "override_callback_uri is "
        f"<public-url>/surfaces/webhooks/whatsapp/numbers/{entity.phone_number_id}."
    )
    return 0


async def _retire(phone_number_id: str) -> int:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        number = await WhatsAppNumberRepository(uow).retire(phone_number_id)
        await uow.commit()
    if number is None:
        print(f"No number with phone_number_id {phone_number_id}.", file=sys.stderr)
        return 1
    print(f"Retired {number.display_phone_number}; whoever holds it keeps it.")
    return 0


async def _remove(phone_number_id: str) -> int:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        removed = await WhatsAppNumberRepository(uow).remove(phone_number_id)
        await uow.commit()
    if not removed:
        print(f"No number with phone_number_id {phone_number_id}.", file=sys.stderr)
        return 1
    print(
        f"Removed {phone_number_id}. Surfaces still naming it keep naming it; "
        "nothing cascades."
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="every number the deployment owns")

    add = subparsers.add_parser(
        "add",
        help=(
            "add a number; secrets come from "
            f"{_ACCESS_TOKEN_VAR}, {_APP_SECRET_VAR} and {_VERIFY_TOKEN_VAR}"
        ),
    )
    add.add_argument("--phone-number-id", required=True)
    add.add_argument("--display-phone-number", required=True)
    add.add_argument("--waba-id", required=True)
    add.add_argument("--onboarding-email-flow-id")
    add.add_argument("--onboarding-code-flow-id")
    add.add_argument("--notes")

    for name, help_text in (
        ("retire", "stop handing this number out; the holder keeps it"),
        ("remove", "drop a number the deployment no longer owns"),
    ):
        subparser = subparsers.add_parser(name, help=help_text)
        subparser.add_argument("--phone-number-id", required=True)

    return parser


async def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "list":
        return await _list()
    if arguments.command == "add":
        return await _add(arguments)
    if arguments.command == "retire":
        return await _retire(arguments.phone_number_id)
    return await _remove(arguments.phone_number_id)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
