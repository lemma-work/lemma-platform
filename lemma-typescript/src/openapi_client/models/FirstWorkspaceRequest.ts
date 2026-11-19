/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type FirstWorkspaceRequest = {
    /**
     * Skip their company's organization, even when it is open to their work domain, and make a workspace of their own. It is made by invitation only and named after the person. Invitations and an organization they already belong to still come first.
     */
    own_organization?: boolean;
    /**
     * What kind of organization to make when one is made. Omitted: a company's, named after it and open to colleagues from the same domain, when the person's address is verified and on a work domain; otherwise a workspace of one. false: a workspace of one whatever the address, for a deployment nobody else can reach.
     */
    team?: (boolean | null);
    /**
     * Make a private pod and assistant when the person would otherwise land in no Lemma: an organization that has no Lemma of its own, or one whose Lemma they left. A new organization is always made with one.
     */
    with_pod?: boolean;
};
