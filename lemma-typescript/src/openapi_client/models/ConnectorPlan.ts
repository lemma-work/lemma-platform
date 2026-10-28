/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountOption } from './AccountOption.js';
import type { ConnectorPlanStatus } from './ConnectorPlanStatus.js';
import type { InstallationOption } from './InstallationOption.js';
export type ConnectorPlan = {
    account_options?: Array<AccountOption>;
    auth_config_id?: (string | null);
    can_create_installation?: boolean;
    connector: string;
    installations?: Array<InstallationOption>;
    message?: (string | null);
    mode: ConnectorPlan.mode;
    purpose?: (string | null);
    required?: boolean;
    slug: string;
    status: ConnectorPlanStatus;
    used_by?: Array<string>;
    visibility?: ConnectorPlan.visibility;
};
export namespace ConnectorPlan {
    export enum mode {
        PER_MEMBER = 'PER_MEMBER',
        SHARED = 'SHARED',
    }
    export enum visibility {
        POD = 'POD',
        RESTRICTED = 'RESTRICTED',
    }
}
