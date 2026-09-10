/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WebLoginKind } from './WebLoginKind.js';
export type WebLoginResponse = {
    created_at: string;
    expires_hint_at: (string | null);
    /**
     * Whether a password is stored as well as a session.
     */
    has_password: boolean;
    id: string;
    kind: WebLoginKind;
    label: string;
    last_used_at: (string | null);
    origin: string;
    updated_at: string;
};
