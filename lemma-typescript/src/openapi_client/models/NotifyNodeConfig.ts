/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Who to tell, and what.
 *
 * Distinct from a FORM node: a form *blocks* the run until somebody answers,
 * which is right when the run needs their input and wrong when it merely needs
 * them informed. This node never suspends.
 */
export type NotifyNodeConfig = {
    /**
     * What to say. Supports the same expression interpolation as other node inputs, so it can carry values from earlier steps.
     */
    message: string;
    /**
     * Pod member to notify.
     */
    recipient_user_id?: (string | null);
    /**
     * Optional JMESPath expression resolving to a pod member id. Takes precedence over recipient_user_id.
     */
    recipient_user_id_expression?: (string | null);
    /**
     * Optional short subject line for the inbox.
     */
    title?: (string | null);
};
