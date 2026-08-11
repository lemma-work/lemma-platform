from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.web_search.search_client import (
    SearchClient,
    SearchFreshness,
    SearchResult,
    SearchVertical,
)
from app.core.log.log import get_logger

# For agentic search
logger = get_logger(__name__)


# Input/output models for web search functions
class WebSearchRequest(BaseModel):
    """Request model for standard web search"""

    query: str = Field(
        ...,
        description=(
            "Search query. Use specific keywords rather than a question, and "
            "prefer `include_domains`/`exclude_domains` over typing `site:` "
            "yourself."
        ),
    )
    max_results: int = Field(
        10, description="Maximum number of search results to return"
    )
    vertical: SearchVertical = Field(
        SearchVertical.WEB,
        description=(
            "What to search: `web` pages, `news` articles, `images`, or "
            "`videos`. Not every provider serves every vertical; if the one "
            "configured here cannot, you get web results and a note saying so."
        ),
    )
    freshness: Optional[SearchFreshness] = Field(
        None,
        description=(
            "Only results from the past `day`, `week`, `month`, or `year`. Use "
            "it for anything time-sensitive — search engines happily return "
            "five-year-old pages for current questions."
        ),
    )
    include_domains: Optional[List[str]] = Field(
        None,
        description="Restrict results to these domains, e.g. ['arxiv.org'].",
    )
    exclude_domains: Optional[List[str]] = Field(
        None,
        description="Drop results from these domains, e.g. ['pinterest.com'].",
    )


class WebSearchResponse(BaseModel):
    """Response model for standard web search"""

    success: bool = Field(..., description="Whether the search was successful")
    results: List[SearchResult] = Field(
        default_factory=list, description="List of search results"
    )
    message: Optional[str] = Field(None, description="Status message")
    note: Optional[str] = Field(
        None,
        description=(
            "Set when the search could not be run exactly as asked — for "
            "example a vertical this provider does not serve."
        ),
    )
    error: Optional[str] = Field(
        None, description="Error message if the search was not successful"
    )


async def search_web(request: WebSearchRequest) -> WebSearchResponse:
    """
    Perform a standard web search using the specified search engine.

    Args:
        request: WebSearchRequest object containing search parameters

    Returns:
        WebSearchResponse object with search results
    """
    try:
        search_client = SearchClient()

        results, note = await search_client.search(
            query=request.query,
            max_results=request.max_results,
            vertical=request.vertical,
            freshness=request.freshness,
            include_domains=request.include_domains,
            exclude_domains=request.exclude_domains,
        )

        return WebSearchResponse(
            success=True,
            results=results,
            message="Web search completed successfully",
            note=note,
        )

    except Exception:
        logger.debug("web_search.request.failed", exc_info=True)
        return WebSearchResponse(success=False, message="Web search failed")
