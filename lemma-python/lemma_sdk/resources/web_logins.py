from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.web_logins import (
    web_login_delete,
    web_login_history,
    web_login_list,
    web_login_sign_in_request_decline,
    web_login_sign_in_request_finish,
    web_login_sign_in_request_get,
)
from ..openapi_client.models.finish_sign_in_request import FinishSignInRequest
from ..openapi_client.models.sign_in_request_response import SignInRequestResponse
from ..openapi_client.models.web_login_audit_response import (
    WebLoginAuditResponse,
)
from ..openapi_client.models.web_login_list_response import WebLoginListResponse
from ..openapi_client.models.web_login_response import WebLoginResponse
from .base import Resource


class WebLogins(Resource):
    """Sites you have signed in to on an agent's behalf.

    Saved logins belong to a person, not to a pod: they are one human's identity
    at a site. So nothing here takes a pod or an organization — the session
    decides whose they are.

    No method returns a stored session, at any privilege level, including to the
    person who created it. The listed shape has no field to put one in.
    """

    def list(self) -> WebLoginListResponse:
        """Every site with a saved login, and whether each still works."""
        return self._call(web_login_list)

    def remove(self, origin: str) -> WebLoginResponse:
        """Forget a site.

        This is the whole of the revocation on Lemma's side. It does **not**
        sign you out at the site: a session that has been deleted here is still
        valid there until you log out or it expires.
        """
        return self._call(web_login_delete, origin=origin)

    def history(self, *, limit: int | None = None) -> WebLoginAuditResponse:
        """What has been done with your saved logins, and by which agent."""
        kwargs = {} if limit is None else {"limit": limit}
        return self._call(web_login_history, **kwargs)

    # ------------------------------------------------------------------
    # Sign-in requests
    # ------------------------------------------------------------------

    def sign_in_request(self, request_id: str | UUID) -> SignInRequestResponse:
        """What an agent is asking you to sign in to, and why."""
        return self._call(web_login_sign_in_request_get, UUID(str(request_id)))

    def finish_sign_in(
        self, request_id: str | UUID, *, force: bool = False
    ) -> SignInRequestResponse:
        """Say you have signed in, so the waiting run can carry on.

        Refused with a 409 when the browser holds nothing for the site, which
        usually means the sign-in did not complete. `force` overrides that for
        sites the check reads wrongly.
        """
        return self._call(
            web_login_sign_in_request_finish,
            UUID(str(request_id)),
            body=FinishSignInRequest(force=force),
        )

    def decline_sign_in(self, request_id: str | UUID) -> SignInRequestResponse:
        """Say you cannot sign in, so the agent stops waiting and says so."""
        return self._call(web_login_sign_in_request_decline, UUID(str(request_id)))
