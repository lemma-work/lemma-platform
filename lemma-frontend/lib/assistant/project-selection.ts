/**
 * The project a conversation works in, as the frontend passes it around.
 *
 * A "project" is a GitHub repository the agent works inside: the conversation
 * carries `metadata.repo`, and the backend derives the agent's working
 * directory from it (`/workspace/repos/{owner}/{repo}`) and clones it before
 * the first command runs. These are the two directions of that one fact —
 * writing a choice into conversation metadata, and reading it back off a
 * conversation that already has one.
 *
 * Kept free of React so the shape of what we send is testable on its own.
 */

export interface ProjectSelection {
  owner: string;
  repo: string;
  ref?: string;
  accountId?: string;
}

export const projectLabel = (project: ProjectSelection): string =>
  `${project.owner}/${project.repo}`;

/** What rides along on the first message. Mirrors the backend's metadata.repo. */
export const projectConversationMetadata = (
  project: ProjectSelection | null,
): Record<string, unknown> | undefined =>
  project
    ? {
        repo: {
          owner: project.owner,
          repo: project.repo,
          ...(project.ref ? { ref: project.ref } : {}),
          ...(project.accountId ? { account_id: project.accountId } : {}),
        },
      }
    : undefined;

/**
 * The project a conversation is already bound to, read back off its metadata.
 *
 * The backend rewrites `repo` from its own parsed form at creation, so anything
 * here has already been validated; this only has to survive the trip through
 * `unknown`.
 */
export const projectFromMetadata = (metadata: unknown): ProjectSelection | null => {
  if (!metadata || typeof metadata !== "object") return null;
  const repo = (metadata as { repo?: unknown }).repo;
  if (!repo || typeof repo !== "object") return null;
  const record = repo as Record<string, unknown>;
  const owner = typeof record.owner === "string" ? record.owner : null;
  const name = typeof record.repo === "string" ? record.repo : null;
  if (!owner || !name) return null;
  return {
    owner,
    repo: name,
    ref: typeof record.ref === "string" ? record.ref : undefined,
    accountId: typeof record.account_id === "string" ? record.account_id : undefined,
  };
};
