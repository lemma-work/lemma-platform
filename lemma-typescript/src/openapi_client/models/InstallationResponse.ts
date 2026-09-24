/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What this installation is, and what the caller is to it.
 */
export type InstallationResponse = {
    /**
     * ``desktop`` for a Lemma Desktop installation on one person's computer, ``server`` for hosted and self-hosted deployments.
     */
    deployment: InstallationResponse.deployment;
    /**
     * Whether the caller is this installation's owner: the first account created on a Desktop installation. Always false on ``server``.
     */
    is_owner: boolean;
    /**
     * Who may create a new account on this installation.
     */
    signup_mode: InstallationResponse.signup_mode;
};
export namespace InstallationResponse {
    /**
     * ``desktop`` for a Lemma Desktop installation on one person's computer, ``server`` for hosted and self-hosted deployments.
     */
    export enum deployment {
        SERVER = 'server',
        DESKTOP = 'desktop',
    }
    /**
     * Who may create a new account on this installation.
     */
    export enum signup_mode {
        OPEN = 'open',
        INVITE_ONLY = 'invite_only',
        CLOSED = 'closed',
    }
}
