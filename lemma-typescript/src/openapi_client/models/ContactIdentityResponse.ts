/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IdentityKind } from './IdentityKind.js';
import type { IdentityStrength } from './IdentityStrength.js';
export type ContactIdentityResponse = {
    kind: IdentityKind;
    strength: IdentityStrength;
    value: string;
    verified_at: string;
};
