/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HostHello } from './HostHello.js';
export type AgentHostPairingComplete = {
    display_name: string;
    hello: HostHello;
    nonce: string;
    pairing_code: string;
    public_key: string;
    signature: string;
    timestamp: number;
};
