import { record, words } from "../data/wire";

/** The GitHub repository a chat works in, read off the conversation's own
 *  directory (`workspace_cwd`): the conversation answers no field naming it.
 *  A chat opened in a repository works in a worktree of it,
 *  `{workspace}/worktrees/{owner}/{name}/{slug}`; one made before worktrees
 *  works in the shared checkout, `{workspace}/repos/{owner}/{name}` (the
 *  backend's `project_repo.py`, and the web's `computer/machine.ts`, which
 *  reads the same layout). Null for any other directory. Pure. */

const IN_REPOSITORY = /\/lemma\/(?:worktrees\/([^/]+)\/([^/]+)\/[^/]+|repos\/([^/]+)\/([^/]+))\/?$/;
/* GitHub's own naming rules, as the backend holds them: a path that only
   looks like the layout is not read as a repository. */
const OWNER = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/;
const NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/;

export function repositoryOf(conversation: unknown): string | null {
    const cwd = words(record(conversation).workspace_cwd);
    const found = cwd ? IN_REPOSITORY.exec(cwd) : null;
    if (!found) return null;
    const owner = found[1] ?? found[3];
    const name = found[2] ?? found[4];
    return OWNER.test(owner) && NAME.test(name) ? `${owner}/${name}` : null;
}

/** The create's part for a repository: "owner/name" as GitHub spells it,
 *  or nothing for a chat in none. */
export function repositoryPayload(repository: string | null): { repository?: string } {
    return repository ? { repository } : {};
}
