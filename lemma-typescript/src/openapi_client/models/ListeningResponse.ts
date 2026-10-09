/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How a schedule on a connected MCP server's event is hearing from it.
 */
export type ListeningResponse = {
    last_error?: (string | null);
    last_event_at?: (string | null);
    refresh_before?: (string | null);
    /**
     * listening: the server holds the subscription. retrying: renewing it failed and is being retried. lapsed: the server no longer tells us anything. pending: the server has not answered yet.
     */
    state: ListeningResponse.state;
};
export namespace ListeningResponse {
    /**
     * listening: the server holds the subscription. retrying: renewing it failed and is being retried. lapsed: the server no longer tells us anything. pending: the server has not answered yet.
     */
    export enum state {
        LISTENING = 'listening',
        RETRYING = 'retrying',
        LAPSED = 'lapsed',
        PENDING = 'pending',
    }
}
