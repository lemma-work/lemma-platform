/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Pod-wide defaults for the pod's DATASTORE triggers.
 */
export type PodTriggerSettings = {
    /**
     * What a bulk write does to this pod's triggers that do not say: skip (recorded as skipped), each (fire per matching row) or batch (one fire with the rows). Omitted means skip.
     */
    bulk_writes_default?: ('skip' | 'each' | 'batch' | null);
};
