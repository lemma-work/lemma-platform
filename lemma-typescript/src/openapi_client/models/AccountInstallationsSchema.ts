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
    /**
     * With PENDING_APPROVAL, whose act it waits on: `github_admin` (an admin of the GitHub account it is installed on), `github_permission` (an owner accepting the App's Members: read on GitHub), or `lemma_admin` (someone who may install apps for this organization).
     */
    waiting_on?: (string | null);
};
