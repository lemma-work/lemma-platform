from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.web_search.search_client import SearchClient, SearchResult
from app.core.log.log import get_logger

# For agentic search
logger = get_logger(__name__)

# Input/output models for web search functions
class WebSearchRequest(BaseModel):
    """Request model for standard web search"""

    query: str = Field(..., description="Search query string")
    max_results: int = Field(
        10, description="Maximum number of search results to return"
    )


class WebSearchResponse(BaseModel):
    """Response model for standard web search"""

    success: bool = Field(..., description="Whether the search was successful")
    results: List[SearchResult] = Field(
        default_factory=list, description="List of search results"
    )
    message: Optional[str] = Field(None, description="Status message")
    error: Optional[str] = Field(None, description="Error message if the search was not successful")


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

        # Perform the search
        results = await search_client.search(
            query=request.query, max_results=request.max_results
        )

        return WebSearchResponse(
            success=True, results=results, message="Web search completed successfully"
        )

    except Exception as e:
        logger.error(f"Error in web search: {str(e)}")
        return WebSearchResponse(
            success=False, message=f"Error performing web search: {str(e)}"
        )
