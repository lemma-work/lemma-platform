from app.modules.agent.tools.context import BaseAgentContext

from app.core.web_search.web_search import (
    WebSearchRequest,
    WebSearchResponse,
    search_web,
)


async def web_search_internal(
    deps: BaseAgentContext, request: WebSearchRequest
) -> WebSearchResponse:
    """The search itself; `search_web` reports its own failures as results."""
    del deps
    return await search_web(request)
