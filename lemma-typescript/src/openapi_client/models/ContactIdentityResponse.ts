/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IdentityKind } from './IdentityKind.js';
import type { IdentityStrength } from './IdentityStrength.js';
export type ContactIdentityResponse = {
    kind: IdentityKind;
    /**
     * They asked the pod to stop writing to this handle (STOP, an unsubscribe link, a bounce or a complaint): nothing is sent to it until they write from it again.
     */
    opted_out?: boolean;
    strength: IdentityStrength;
    value: string;
    verified_at: string;
};
