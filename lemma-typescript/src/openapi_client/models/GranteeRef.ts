/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type GranteeRef = {
    grantee_id: string;
    grantee_type: GranteeRef.grantee_type;
};
export namespace GranteeRef {
    export enum grantee_type {
        ROLE = 'ROLE',
        POD_MEMBER = 'POD_MEMBER',
    }
}
