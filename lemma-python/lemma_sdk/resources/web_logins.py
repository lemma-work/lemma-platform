from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.web_logins import (
    web_login_delete,
    web_login_history,
    web_login_list,
    web_login_sign_in_answer,
    web_login_sign_in_pending,
)
from ..openapi_client.models.answer_sign_in_request import AnswerSignInRequest
from ..openapi_client.models.pending_sign_in_response import PendingSignInResponse
from ..openapi_client.models.sign_in_outcome_response import SignInOutcomeResponse
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

    def list(
        self, *, limit: int | None = None, page_token: str | None = None
    ) -> WebLoginListResponse:
        """One page of sites with a saved login, and whether each still works.

        Follow ``next_page_token`` to see the rest: a full page is not itself
        proof that more exist, and a login you cannot list is one you cannot
        revoke.
        """
        kwargs: dict[str, object] = {}
        if limit is not None:
            kwargs["limit"] = limit
        if page_token is not None:
            kwargs["page_token"] = page_token
        return self._call(web_login_list, **kwargs)

    def remove(self, origin: str) -> WebLoginResponse:
        """Forget a site.

        This is the whole of the revocation on Lemma's side. It does **not**
        sign you out at the site: a session that has been deleted here is still
        valid there until you log out or it expires.
        """
        return self._call(web_login_delete, origin=origin)

    def history(
        self, *, limit: int | None = None, page_token: str | None = None
    ) -> WebLoginAuditResponse:
        """What has been done with your saved logins, and by which agent."""
        kwargs: dict[str, object] = {}
        if limit is not None:
            kwargs["limit"] = limit
        if page_token is not None:
            kwargs["page_token"] = page_token
        return self._call(web_login_history, **kwargs)

    # ------------------------------------------------------------------
    # Sign-in requests
    # ------------------------------------------------------------------

    def pending_sign_in(
        self, conversation_id: UUID | str, tool_call_id: str
    ) -> PendingSignInResponse:
        """What a sign-in link is asking for.

        Addressed by the pause it belongs to -- the conversation and the tool
        call that is waiting -- rather than by a row. There is no status to read:
        a link nothing is waiting on is a 404.
        """
        return self.generated(
            web_login_sign_in_pending.sync_detailed,
            conversation_id=str(conversation_id),
            tool_call_id=tool_call_id,
        )

    def answer_sign_in(
        self,
        conversation_id: UUID | str,
        tool_call_id: str,
        *,
        signed_in: bool,
        force: bool = False,
    ) -> SignInOutcomeResponse:
        """Say whether you signed in, so the waiting run can carry on.

        `force` saves whatever the browser holds even when it does not look
        signed in, for sites the check reads wrongly.
        """
        return self.generated(
            web_login_sign_in_answer.sync_detailed,
            conversation_id=str(conversation_id),
            tool_call_id=tool_call_id,
            body=AnswerSignInRequest(signed_in=signed_in, force=force),
        )
