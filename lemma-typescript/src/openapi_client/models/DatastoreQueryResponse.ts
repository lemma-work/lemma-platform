/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for read-only datastore query results.
 */
export type DatastoreQueryResponse = {
    items: Array<Record<string, any>>;
    /**
     * Number of rows in `items`. This is what came back, not how many rows the query matched: when `truncated` is true the result was cut at the deployment's row cap and more rows exist.
     */
    total: number;
    /**
     * True when the row cap cut the result short, so `items` is a prefix of the query's real answer. Narrow the query (add a WHERE, aggregate, or LIMIT) to see the rest. Reported because a capped result is otherwise indistinguishable from a complete one.
     */
    truncated?: boolean;
};
