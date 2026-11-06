/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type HomeSeenRequest = {
    /**
     * The moment to mark, with its offset -- typically when the page loaded, so what arrived while it was open still counts as new. Omitted, now. A moment in the future is read as now, and the mark never moves back.
     */
    seen_at?: (string | null);
};
