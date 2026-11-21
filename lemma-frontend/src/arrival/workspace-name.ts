/** What a workspace of one is called when the server had no name to put on
 *  it (the server's `UNNAMED_PERSONAL_ORGANIZATION`). Named after the person
 *  as soon as they give a name here. */
export const UNNAMED_WORKSPACE = "My workspace";

/** The name a workspace of one should carry once the person has said theirs:
 *  only one the server left unnamed, and only with a name to give it. */
export function workspaceNameFor(orgName: string, first: string): string | null {
    const name = first.trim();
    if (!name || orgName !== UNNAMED_WORKSPACE) return null;
    return `${name}’s workspace`;
}
