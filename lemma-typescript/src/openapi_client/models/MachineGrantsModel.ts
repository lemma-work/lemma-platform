/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Who may use a pod machine beyond what visibility already allows.
 *
 * Agents and functions by name; people by user id. A RESTRICTED machine is
 * usable only by those named here (and pod admins).
 */
export type MachineGrantsModel = {
    agents?: Array<string>;
    functions?: Array<string>;
    members?: Array<string>;
};
