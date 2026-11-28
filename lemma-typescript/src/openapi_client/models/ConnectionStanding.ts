/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What the caller can do about a connector right now.
 *
 * By mode: PER_MEMBER is ``ready``, ``connect`` (no account of theirs on the
 * installation anywhere), ``use_existing`` (one in the organization, not in
 * this pod yet) or ``reconnect``; SHARED and APP are ``ready`` or ``waiting``
 * (no account shared; the App not installed); any mode is ``waiting`` when
 * its installation is disabled, and ``not_allowed`` without ``connector.use``.
 * GitHub's PER_MEMBER and SHARED are ``install`` while the account in use
 * reaches no GitHub App installation this organization holds; ``github``
 * on ``me`` says why and what unblocks it.
 */
export enum ConnectionStanding {
    READY = 'ready',
    INSTALL = 'install',
    CONNECT = 'connect',
    RECONNECT = 'reconnect',
    USE_EXISTING = 'use_existing',
    WAITING = 'waiting',
    NOT_ALLOWED = 'not_allowed',
}
