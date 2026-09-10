/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InstallationChoiceSchema } from './InstallationChoiceSchema.js';
/**
 * What an account can actually reach, asked of the provider.
 */
export type AccountInstallationsSchema = {
    choices?: Array<InstallationChoiceSchema>;
    install_state: string;
    installation_id?: (string | null);
};
