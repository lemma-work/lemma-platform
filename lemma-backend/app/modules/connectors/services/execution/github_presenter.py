"""Which of a GitHub App's two identities runs a given operation.

The App can act as itself against an installation, or as the person who
authorized it. Both are wanted, for different work, and the choice is not a
setting anyone maintains: every operation already carries `github_token_kind`,
generated from GitHub's own `x-github.enabledForGitHubApps`. So the answer comes
from GitHub, and stays right when GitHub changes its mind.

Two questions, not one, and conflating them was a bug this caught late:

**What the caller should be** is `request.act_as`, and it defaults to "user".
An agent's operations ask for "app" so a schedule outlives the person who set it
up. Pod publish, pod import and anything else that says nothing keep acting as
the person -- which is what makes the published repository's commits carry their
name rather than a bot's, and what keeps import able to read a repository the
App was never installed on.

**What the App is permitted to do** is `github_token_kind`, generated from
GitHub's own `x-github.enabledForGitHubApps`. Fourteen routes (`/user/...`,
gists) an installation token cannot reach at all, so even a caller asking for
"app" gets the person's token there.

So the App's token is used only when the caller asked for it, the route allows
it, and the account knows its installation. Anything else is the person's token
-- including an install nobody has reconnected yet, which keeps working exactly
as it did and gains the App identity the moment it has an installation id.

A function that asked for the app by name ("app_only") gets the app or a
refusal at each of those fallbacks, never the person's token in its place: it
chose the bot, and posting under the person's name instead is the one outcome
nobody asked for (``domain/app_identity.py``).

The sandbox's `git`/`gh` never reaches here at all: it resolves its credential
straight from the account, for the same reason.

An APP pod connector has no account and so no person's token: its calls always
arrive as "app_only", with the installation the call's repository owner picked
(``domain/app_installation.py``). The same strict path serves them.

Whichever way it goes, what the call is presented as is noted
(``note_presented``) for the operation's analytics record.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.log.log import get_logger
from app.modules.connectors.domain.app_identity import (
    NO_INSTALLATION,
    TOKEN_REFUSED,
    USER_ONLY_OPERATION,
    AppIdentityUnavailableError,
    note_presented,
)
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.services.auth.github_app import (
    GitHubAppUnavailable,
    installation_token,
)

logger = get_logger(__name__)

_USER_ONLY = "user_only"
_AS_APP = "app"
_APP_ONLY = "app_only"


#: Mints an installation token for an installation id. GitHub, in production.
InstallationTokenMinter = Callable[[str], Awaitable[str]]


async def _mint(installation_id: str) -> str:
    # Looked up at call time, so a test that patches the module's
    # ``installation_token`` still reaches the presenter it built by default.
    return await installation_token(installation_id)


class GitHubCredentialPresenter:
    def __init__(self, mint: InstallationTokenMinter = _mint) -> None:
        self._mint = mint

    async def present(self, request: ExecutionRequest) -> dict[str, object]:
        strict = request.act_as == _APP_ONLY
        if request.act_as != _AS_APP and not strict:
            # The caller did not ask to be the app. Publishing a pod, importing
            # one, or any path that has not thought about it: acting as the
            # person is both the safe answer and the one they had before.
            note_presented("user")
            return request.credentials

        token_kind = (request.operation.execution or {}).get("github_token_kind")
        if token_kind == _USER_ONLY:
            if strict:
                raise AppIdentityUnavailableError(
                    "GitHub lets only a person make this call.",
                    code=USER_ONLY_OPERATION,
                )
            note_presented("user")
            return request.credentials

        # The *account's* binding, not the install's. A GitHub App installed on
        # two organizations gives their accounts different installations, and
        # the install config is shared by both -- reading it from there would
        # hand one organization's token to the other.
        installation_id = request.account_external_ref
        if not installation_id:
            if strict:
                raise AppIdentityUnavailableError(
                    "this GitHub account has no App installation. Reconnect it "
                    "from the Lemma's connectors.",
                    code=NO_INSTALLATION,
                )
            # An install that has not been reconnected yet. It has a user token
            # and that token still works; going quiet here instead would break
            # every operation on it for the sake of an identity it never had.
            note_presented("user")
            return request.credentials

        try:
            token = await self._mint(str(installation_id))
        except GitHubAppUnavailable as exc:
            if strict:
                raise AppIdentityUnavailableError(
                    "GitHub did not give the App a token for this installation.",
                    code=TOKEN_REFUSED,
                ) from exc
            # No key configured, or GitHub refused -- most often the App was
            # uninstalled since the id was stored. The user token is a real
            # credential and a narrower one; falling back to it degrades the
            # identity rather than the operation.
            logger.info(
                "connectors.github_presenter.fell_back_to_user_token",
                connector_id=request.connector_id,
            )
            note_presented("user")
            return request.credentials

        # `token_type` matters: the executor reads it to build the header, and
        # GitHub's installation tokens are bearer tokens.
        note_presented("app")
        return {**request.credentials, "access_token": token, "token_type": "Bearer"}
