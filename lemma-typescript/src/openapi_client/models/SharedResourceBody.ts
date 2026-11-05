/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ResourceType } from './ResourceType.js';
export type SharedResourceBody = {
    /**
     * Reading only: datastore.table.read and datastore.record.read for a table, folder.read for a folder.
     */
    permission_ids: Array<string>;
    /**
     * The table's name, or the folder's path.
     */
    resource_name: string;
    resource_type: ResourceType;
};
