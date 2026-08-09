/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The Slack settings a *caller* owns.
 *
 * Only ``app_name``. The per-person DM agent map is written from inside Slack
 * — each person picks their own in the App Home — so it is readable here and
 * never writable, which keeps one editor from reassigning everybody.
 */
export type SurfaceSlackConfigInput = {
    app_name?: (string | null);
};
