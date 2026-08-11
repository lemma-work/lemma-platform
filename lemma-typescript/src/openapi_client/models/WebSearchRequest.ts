/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SearchFreshness } from './SearchFreshness.js';
import type { SearchVertical } from './SearchVertical.js';
/**
 * Request model for standard web search
 */
export type WebSearchRequest = {
    /**
     * Drop results from these domains, e.g. ['pinterest.com'].
     */
    exclude_domains?: (Array<string> | null);
    /**
     * Only results from the past `day`, `week`, `month`, or `year`. Use it for anything time-sensitive — search engines happily return five-year-old pages for current questions.
     */
    freshness?: (SearchFreshness | null);
    /**
     * Restrict results to these domains, e.g. ['arxiv.org'].
     */
    include_domains?: (Array<string> | null);
    /**
     * Maximum number of search results to return
     */
    max_results?: number;
    /**
     * Search query. Use specific keywords rather than a question, and prefer `include_domains`/`exclude_domains` over typing `site:` yourself.
     */
    query: string;
    /**
     * What to search: `web` pages, `news` articles, `images`, or `videos`. Not every provider serves every vertical; if the one configured here cannot, you get web results and a note saying so.
     */
    vertical?: SearchVertical;
};
