import { codeOf } from "@/data/trouble";
import type { ChatRepository } from "@/data/types";

/** What the repository chip says and how it lists, apart from how it is
 *  drawn: the web's chip (`repository-chip.tsx`) and the phone's sheet both
 *  read it. Pure. */

/** The refusals the reader can fix on the Connectors page, said as what to
 *  do there. Any other -- the pod has no `github` connector, GitHub is not
 *  answering -- draws nothing: a chat without a repository is the ordinary
 *  one, and there is no step to offer. */
const FIXES: Record<string, string> = {
    GITHUB_INSTALLATION_REQUIRED: "Install the GitHub app",
    GITHUB_INSTALLATION_UNREACHABLE: "Reconnect GitHub",
    CONNECTION_REQUIRED: "Connect GitHub",
    CONNECTOR_NEEDS_ACCOUNT: "Connect GitHub",
    CONNECTOR_REAUTH_REQUIRED: "Reconnect GitHub",
};

/** The step a refused listing asks of the reader, or null when it asks
 *  none and the chip draws nothing. */
export function repositoryFix(problem: unknown): string | null {
    return FIXES[codeOf(problem)] ?? null;
}

/** The chip, when a step is missing: "Connect GitHub to work in a repo". */
export function fixLabel(fix: string): string {
    return `${fix} to work in a repo`;
}

/** Said about a refusal that carried no words of its own. */
export const FIX_FALLBACK = "GitHub is connected but cannot reach a repository yet.";

/** The first choice: a chat in no repository, which is the ordinary one. */
export const NO_REPOSITORY = "No repository";

/** The installation covers more than GitHub listed in one read; those are
 *  not offered. */
export const MORE_INSTALLED = "More are installed than shown";

/** What the chip is for, said as it stands. */
export function repositoryTitle(picked: string | null): string {
    return picked ? `Works in ${picked}, on a branch of its own` : "Work in a GitHub repository";
}

/** The repositories under each GitHub account they live on -- someone's own
 *  and their GitHub organization's -- in the order listed. */
export function byOwner(items: readonly ChatRepository[]): [string, ChatRepository[]][] {
    const owners = new Map<string, ChatRepository[]>();
    for (const repo of items) {
        const owner = repo.fullName.split("/")[0];
        owners.set(owner, [...(owners.get(owner) ?? []), repo]);
    }
    return [...owners];
}
