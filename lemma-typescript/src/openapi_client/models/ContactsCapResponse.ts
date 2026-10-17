/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What the organization lets its bots spend answering contacts, a month.
 */
export type ContactsCapResponse = {
    /**
     * How many contacts its bots have answered so far.
     */
    answered_contacts?: number;
    /**
     * Nobody in the organization set a cap, so the deployment's default applies.
     */
    is_default?: boolean;
    /**
     * How many contacts the organization's bots may answer at all. Absent for no limit.
     */
    max_contacts?: (number | null);
    /**
     * The cap that applies this month. Absent when an owner removed it: no limit.
     */
    monthly_limit_usd?: (number | null);
    organization_id: string;
    /**
     * Spent this calendar month (UTC) answering contacts and people outside the pod in groups, on models Lemma provides.
     */
    spent_this_month_usd: number;
};
