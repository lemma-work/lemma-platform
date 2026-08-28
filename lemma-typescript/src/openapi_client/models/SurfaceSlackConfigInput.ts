/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The Slack settings a *caller* owns.
 *
 * Not the per-person DM agent map: that is written from inside Slack — each
 * person picks their own in the App Home — so it is readable here and never
 * writable, which keeps one editor from reassigning everybody.
 *
 * ``dedicated_to_agent`` is the caller's, though, and has to be: it says this
 * app was made as one agent's own bot, which is a fact about why the app
 * exists and cannot be read off the surface. Setting it is what withdraws the
 * per-person choice, so it is the one Slack setting that decides whether the
 * other is offered at all.
 */
export type SurfaceSlackConfigInput = {
    app_name?: (string | null);
    dedicated_to_agent?: boolean;
};
