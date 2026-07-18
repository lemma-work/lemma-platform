
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.web.web import (
    web_search_internal,
)
from app.core.log.log import get_logger
from app.core.web_search.web_search import WebSearchRequest, WebSearchResponse

logger = get_logger(__name__)


async def web_search(
    ctx: RunContext[BaseAgentContext], request: WebSearchRequest
) -> WebSearchResponse:
    """
    Performs web search and returns raw search results with snippets and URLs.

    USAGE GUIDELINES:
    - Use for finding current information, news, and specific topics
    - Finding youtube videos, images or any assets online
    - Returns structured search results with titles, snippets, and URLs
    - Combine with save_url_as_file to archive interesting results
    - Use specific, targeted search queries for better results

    BEST PRACTICES:
    - Use specific keywords rather than questions
    - Include relevant terms like dates, locations, or technical terms

    EXAMPLES:
    - Technical search: "Python async programming best practices 2025"
    - News search: "artificial intelligence regulations European Union"
    - Research search: "renewable energy storage solutions lithium alternatives"

    """
    try:
        return await web_search_internal(ctx.deps, request)
    except Exception as e:
        logger.debug("agent.web_search.failed", exc_info=True)
        return WebSearchResponse(
            success=False,
            error=f"Web search failed: {e}",
        )


web_search_toolset = FunctionToolset[BaseAgentContext](
    tools=[
        web_search,
    ]
)
