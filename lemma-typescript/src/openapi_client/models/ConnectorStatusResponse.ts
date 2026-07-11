/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectedAccountSummary } from './ConnectedAccountSummary.js';
import type { InstalledAppSummary } from './InstalledAppSummary.js';
export type ConnectorStatusResponse = {
    accounts: Array<ConnectedAccountSummary>;
    installed: Array<InstalledAppSummary>;
};
