/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceSetupActionField } from './SurfaceSetupActionField.js';
/**
 * A concrete thing the user must do to finish wiring up a surface.
 *
 * Only emitted when the user actually has to act (custom/bring-your-own-app
 * credentials, or a pending OAuth grant). Each action carries where to go
 * (``link``), ordered ``steps``, and the values to paste (``fields``).
 */
export type SurfaceSetupAction = {
    description: string;
    fields?: Array<SurfaceSetupActionField>;
    key: string;
    link?: (string | null);
    link_label?: (string | null);
    steps?: Array<string>;
    title: string;
};
