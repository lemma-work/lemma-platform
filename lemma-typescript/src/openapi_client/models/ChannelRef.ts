/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelKind } from './ChannelKind.js';
import type { PersonRef } from './PersonRef.js';
export type ChannelRef = {
    id: string;
    kind: ChannelKind;
    person?: (PersonRef | null);
    title?: (string | null);
};
