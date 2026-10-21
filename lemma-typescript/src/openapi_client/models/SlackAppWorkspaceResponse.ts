/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A Slack workspace Lemma can create apps in. Carries no token.
 */
export type SlackAppWorkspaceResponse = {
    /**
     * Slack no longer accepts the saved token, or Lemma can no longer read it: paste a new one before Lemma can create apps here.
     */
    needs_new_token?: boolean;
    saved_at: string;
    /**
     * The Slack user whose configuration token this is. Slack creates every app as them, and the token can change every app they collaborate on.
     */
    slack_user_id?: (string | null);
    team_id: string;
    team_name?: (string | null);
};
