/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectionAccount } from './ConnectionAccount.js';
import type { ConnectionGithub } from './ConnectionGithub.js';
import type { ConnectionStanding } from './ConnectionStanding.js';
import type { ExistingAccount } from './ExistingAccount.js';
export type ConnectionMe = {
    account?: (ConnectionAccount | null);
    /**
     * Set with `use_existing`: the account to add.
     */
    existing?: (ExistingAccount | null);
    /**
     * Set with `install`.
     */
    github?: (ConnectionGithub | null);
    standing: ConnectionStanding;
};
