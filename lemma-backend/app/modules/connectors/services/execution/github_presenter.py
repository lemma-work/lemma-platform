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

The sandbox's `git`/`gh` never reaches here at all: it resolves its credential
straight from the account, for the same reason.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.services.auth.github_app import (
    GitHubAppUnavailable,
    installation_token,
)

logger = get_logger(__name__)

_USER_ONLY = "user_only"
_AS_APP = "app"


class GitHubCredentialPresenter:
    async def present(self, request: ExecutionRequest) -> dict[str, object]:
        if request.act_as != _AS_APP:
            # The caller did not ask to be the app. Publishing a pod, importing
            # one, or any path that has not thought about it: acting as the
            # person is both the safe answer and the one they had before.
            return request.credentials

        token_kind = (request.operation.execution or {}).get("github_token_kind")
        if token_kind == _USER_ONLY:
            return request.credentials

        # The *account's* binding, not the install's. A GitHub App installed on
        # two organizations gives their accounts different installations, and
        # the install config is shared by both -- reading it from there would
        # hand one organization's token to the other.
        installation_id = request.account_external_ref
        if not installation_id:
            # An install that has not been reconnected yet. It has a user token
            # and that token still works; going quiet here instead would break
            # every operation on it for the sake of an identity it never had.
            return request.credentials

        try:
            token = await installation_token(str(installation_id))
        except GitHubAppUnavailable:
            # No key configured, or GitHub refused -- most often the App was
            # uninstalled since the id was stored. The user token is a real
            # credential and a narrower one; falling back to it degrades the
            # identity rather than the operation.
            logger.info(
                "connectors.github_presenter.fell_back_to_user_token",
                connector_id=request.connector_id,
            )
            return request.credentials

        # `token_type` matters: the executor reads it to build the header, and
        # GitHub's installation tokens are bearer tokens.
        return {**request.credentials, "access_token": token, "token_type": "Bearer"}
