/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What the importer chose for one bundled connector.
 *
 * Leave it all out to take the plan's default installation and share no
 * account. ``auth_config_id`` and ``create_installation`` are alternatives.
 */
export type ConnectorChoice = {
    /**
     * One of the plan's `installations` for this connector.
     */
    auth_config_id?: (string | null);
    /**
     * Install Lemma's own app for the connector in your organization and use it. Offered only where the plan says `can_create_installation`.
     */
    create_installation?: boolean;
    /**
     * SHARED only: one of your own accounts (the plan's `account_options`) to share with the pod for this connector to run as. Left out, the connector imports needing an account.
     */
    share_account_id?: (string | null);
};
