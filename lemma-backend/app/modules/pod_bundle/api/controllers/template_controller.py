"""The roles on the hiring shelf.

Not pod-scoped: the shelf is read before the pod it would fill exists. Any
signed-in user may read it -- it lists what ships with the server, and nothing
of anyone's.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.api.dependencies import CurrentUser
from app.modules.pod_bundle.api.schemas import (
    TemplateCardResponse,
    TemplateListResponse,
)
from app.modules.pod_bundle.application.template_catalog import list_template_cards

router = APIRouter(prefix="/pods", tags=["Pod Bundle"], redirect_slashes=False)


@router.get(
    "/bundle/templates",
    response_model=TemplateListResponse,
    operation_id="pod.bundle.templates.list",
    summary="List Role Templates",
    description=(
        "The templates that ship with Lemma and carry a role card, in shelf "
        "order: the job in one line, what arrives, first things to say, standing "
        "work on offer, and what the role is judged on, read from the template "
        "itself. Hire one by importing it with kind=TEMPLATE into a new pod."
    ),
)
async def list_templates(user: CurrentUser) -> TemplateListResponse:
    cards = await list_template_cards()
    return TemplateListResponse(
        items=[TemplateCardResponse.from_domain(card) for card in cards]
    )
