/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DirectoryTreeNode = {
    children?: Array<DirectoryTreeNode>;
    has_markdown?: (boolean | null);
    has_more_files?: boolean;
    indexed?: (boolean | null);
    kind: string;
    name: string;
    path: string;
    status?: (string | null);
    visibility?: (string | null);
};
