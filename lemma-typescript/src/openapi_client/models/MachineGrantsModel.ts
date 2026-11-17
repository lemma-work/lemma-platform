/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Which agents and functions may use a pod machine, by name.
 *
 * A workload uses a pod machine only with this grant, and then only as far
 * as the person it acts for may. People need none: their pod role decides.
 */
export type MachineGrantsModel = {
    agents?: Array<string>;
    functions?: Array<string>;
};
