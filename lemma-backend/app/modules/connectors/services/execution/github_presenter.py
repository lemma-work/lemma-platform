"""Which of a GitHub App's two identities runs a given operation.

The App can act as itself against an installation, or as the person who
authorized it. Both are wanted, for different work, and the choice is not a
setting anyone maintains: every operation already carries `github_token_kind`,
generated from GitHub's own `x-github.enabledForGitHubApps`. So the answer comes
from GitHub, and stays right when GitHub changes its mind.

- `user_only` -- the fourteen routes an installation token cannot reach at all
  (`/user/...`, gists). Always the person's token.
- `installation_ok` -- everything else. The App's token when the install knows
  its installation, the person's when it does not.

That fallback is what makes this safe to deploy before everyone has reconnected:
an install still carrying only a user token keeps working exactly as it did,
and gains the App identity the moment it has an installation id.

Note what does *not* consult this: the sandbox's `git`/`gh` and pod publishing
resolve their credential directly from the account, deliberately, because work
in someone's checkout should carry their name rather than the App's.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.services.auth.github_app import (
    GitHubAppUnavailable,
    installation_token,
)

logger = get_logger(__name__)

#: The install config key holding which installation this account authorized.
INSTALLATION_CONFIG_KEY = "installation_id"

_USER_ONLY = "user_only"


class GitHubCredentialPresenter:
    async def present(self, request: ExecutionRequest) -> dict[str, object]:
        token_kind = (request.operation.execution or {}).get("github_token_kind")
        if token_kind == _USER_ONLY:
            return request.credentials

        installation_id = (request.config or {}).get(INSTALLATION_CONFIG_KEY)
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
