/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileSearchResultSchema } from './FileSearchResultSchema.js';
import type { SearchMethod } from './SearchMethod.js';
export type FileSearchResponse = {
    items: Array<FileSearchResultSchema>;
    query: string;
    search_method: SearchMethod;
    /**
     * Number of matches in `items`. This is what came back, not how many matches the pod holds: when `truncated` is true the result was cut at `limit` and more exist.
     */
    total: number;
    /**
     * True when the result filled the requested `limit`, so there are likely further matches this response does not show. Narrow the query or raise `limit` to see more. Reported because a capped result is otherwise indistinguishable from a complete one — an agent reading `total` as the number of matching documents states it to a person as fact.
     */
    truncated?: boolean;
};
