/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type RowRequest = {
    /**
     * A solved `row` challenge. Every answer from somebody the pod has not confirmed needs its own; a confirmed contact sends none.
     */
    altcha?: (string | null);
    table: string;
    values?: Record<string, any>;
};
