/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DatastoreOperation } from './DatastoreOperation.js';
export type DataStoreWorkflowStartConfigInput = {
    /**
     * Datastore operations that should trigger this flow. One or more of INSERT, UPDATE, DELETE.
     */
    operations?: Array<DatastoreOperation>;
    /**
     * Table name inside the datastore to subscribe to.
     */
    table_name: string;
};
