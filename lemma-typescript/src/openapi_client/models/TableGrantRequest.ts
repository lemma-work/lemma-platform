/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type TableGrantRequest = {
    /**
     * Columns they may fill in a new row; empty to only read.
     */
    add_columns?: Array<string>;
    /**
     * Columns everybody in the group may read.
     */
    read_columns?: Array<string>;
};
