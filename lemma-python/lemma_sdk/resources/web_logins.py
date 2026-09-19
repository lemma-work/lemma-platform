from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.web_logins import (
    web_login_delete,
    web_login_list,
    web_login_sign_in_answer,
    web_login_sign_in_pending,
)
from ..openapi_client.models.answer_sign_in_request import AnswerSignInRequest
from ..openapi_client.models.forget_response import ForgetResponse
from ..openapi_client.models.pending_sign_in_response import PendingSignInResponse
from ..openapi_client.models.sign_in_outcome_response import SignInOutcomeResponse
from ..openapi_client.models.web_login_list_response import WebLoginListResponse
from .base import Resource


class WebLogins(Resource):
    """Sites the agent's browser is signed in to.

    These belong to a person, not to a pod: they are one human's identity at a
    site. So nothing here takes a pod or an organization -- the session decides
    whose browser it is.

    Nothing returns a stored session, and that is structural rather than a
    promise about response shapes: the browser keeps its own profile in the
    sandbox and cookie values never cross that boundary.
    """

    def list(self, *, wake: bool = False) -> WebLoginListResponse:
        """Every site the browser is signed in to.

        Not paged -- this is what one browser holds, not a table that grows.

        ``wake`` is off by default because reading the list means a round trip
        into the sandbox: a paused computer answers ``sleeping`` rather than
        being started by somebody opening a list.
        """
        return self._call(web_login_list, wake=wake)

    def remove(self, origin: str) -> ForgetResponse:
        """Sign the agent's browser out of a site.

        Really signs it out. Its predecessor deleted Lemma's encrypted copy
        and left the browser as it was, which is why every caller had to
        disclaim itself. Needs the computer running, and refuses rather than
        reporting a success it did not achieve.
        """
        return self._call(web_login_delete, origin=origin)

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
    ) -> SignInOutcomeResponse:
        """Say whether you signed in, so the waiting run can carry on.

        Nothing is stored by answering: the browser holds the session, so
        finishing a sign-in is the person finishing it. The reply says whether
        the site stopped asking, which is passed on to the agent.
        """
        return self.generated(
            web_login_sign_in_answer.sync_detailed,
            conversation_id=str(conversation_id),
            tool_call_id=tool_call_id,
            body=AnswerSignInRequest(signed_in=signed_in),
        )
