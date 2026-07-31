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
    total: number;
};
