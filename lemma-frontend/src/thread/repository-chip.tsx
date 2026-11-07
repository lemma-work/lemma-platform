"use client";

import "@/styles/desktop.css";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { codeOf, saidAbout, source, type ChatRepository } from "@/data";
import { isFramePath } from "@/outcomes/paths";
import { BranchIcon, ChevronDownIcon } from "@/ui/icons";

/** The GitHub repositories a new chat in this pod can work in, read once
 *  for every box that offers them. Refused when the pod's `github` connector
 *  cannot give the reader's sandbox a checkout, each refusal with its code. */
export function useChatRepositories(podId: string | null) {
    return useQuery({
        queryKey: ["chat-repositories", podId],
        queryFn: () => source.listChatRepositories(podId as string),
        enabled: Boolean(podId),
        staleTime: 5 * 60_000,
        retry: false,
        /* Read again on coming back to the tab only while refused: the fix
           is made on Connectors, in another one. */
        refetchOnWindowFocus: (query) => query.state.status === "error",
    });
}

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

/** The repository a new chat works in, beside the composer.
 *
 *  Picked before the chat exists and sent with the conversation it creates:
 *  the agent then works in a worktree of its own, on a branch of its own,
 *  cut from the repository. A native list underneath, for the keyboard.
 *
 *  When GitHub is connected but not yet usable, the chip says the step that
 *  is missing and opens Connectors in a tab of its own, so what is typed here
 *  stays; the listing is read again on coming back. */
export function RepositoryChip({ podId, picked, onPick, className, chevron }: {
    podId: string;
    /** "owner/name", or null for a chat in no repository. */
    picked: string | null;
    onPick: (repository: string | null) => void;
    /** Where it sits decides how it looks: in a bar, that bar's own picker
     *  class; left out, the composer's pill on a row of its own. */
    className?: string;
    chevron?: ReactNode;
}) {
    const listed = useChatRepositories(podId);
    const pick = className ?? "repo-chip__pick";
    const row = (chip: ReactNode) => (className ? chip : <div className="repo-chip">{chip}</div>);

    if (listed.error) {
        const fix = FIXES[codeOf(listed.error)];
        /* Connectors is a page of the app's frame; the older shell has no
           address for it to open. */
        if (!fix || !isFramePath(window.location.pathname)) return null;
        return row(
            <a
                className={pick + " repo-chip--fix"}
                href="/connectors"
                target="_blank"
                rel="noopener"
                title={saidAbout(listed.error, "GitHub is connected but cannot reach a repository yet.") + " Opens Connectors in a new tab."}
            >
                <BranchIcon size={14} aria-hidden="true" />
                <span>{fix} to work in a repo</span>
            </a>,
        );
    }

    const items = listed.data?.items ?? [];
    if (items.length === 0) return null;
    const owners = [...items.reduce((byOwner, repo) => {
        const owner = repo.fullName.split("/")[0];
        byOwner.set(owner, [...(byOwner.get(owner) ?? []), repo]);
        return byOwner;
    }, new Map<string, ChatRepository[]>())];
    return row(
        <label className={pick} title={picked ? `Works in ${picked}, on a branch of its own` : "Work in a GitHub repository"}>
            <span className="sr-only">Repository</span>
            <BranchIcon size={14} aria-hidden="true" />
            <select value={picked ?? ""} onChange={(event) => onPick(event.target.value || null)}>
                <option value="">No repository</option>
                {/* Under each GitHub account they live on -- someone's own and
                    their GitHub organization's -- when there is more than one. */}
                {owners.length > 1
                    ? owners.map(([owner, repos]) => (
                        <optgroup key={owner} label={owner}>
                            {repos.map((repo) => <option key={repo.fullName} value={repo.fullName}>{repo.fullName}</option>)}
                        </optgroup>
                    ))
                    : items.map((repo) => <option key={repo.fullName} value={repo.fullName}>{repo.fullName}</option>)}
                {/* The installation covers more than GitHub listed in one
                    read; those are not offered here. */}
                {listed.data?.complete === false && <option disabled>More are installed than shown</option>}
            </select>
            {chevron ?? <ChevronDownIcon size={12} aria-hidden="true" />}
        </label>,
    );
}
