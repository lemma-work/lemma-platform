
from app.modules.agent.tools.context import BaseAgentContext

from app.core.log.log import get_logger
from app.core.web_search.web_search import (
    WebSearchRequest,
    WebSearchResponse,
    search_web,
)

logger = get_logger(__name__)


async def web_search_internal(
    deps: BaseAgentContext, request: WebSearchRequest
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
        return await search_web(request)
    except Exception:
        logger.debug("agent.web_search.failed", exc_info=True)
        return WebSearchResponse(
            success=False,
            error="Request failed due to unexpected error. Please try again.",
        )
