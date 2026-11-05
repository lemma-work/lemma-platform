/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
export type DeliveryTarget = {
    kind: DeliveryTarget.kind;
    /**
     * The member a push went to.
     */
    person?: (PersonRef | null);
    source_id?: (string | null);
};
export namespace DeliveryTarget {
    export enum kind {
        SOURCE = 'source',
        MEMBER = 'member',
    }
}
