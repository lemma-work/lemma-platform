"use client";

import { useQuery } from "@tanstack/react-query";
import { unbound } from "@/thread/conversation-list";
import { source, type Pod, type SpaceView, type Tab } from "@/data";
import { AppsIcon, FileIcon, FolderIcon, GroupsIcon, PlusIcon, SettingsIcon, TableIcon, WorkflowIcon } from "@/ui/icons";
import { sayWaiting, waitingTotal } from "@/data/groups";
import { useGroups } from "./group-queries";
import { useFeature } from "@/site/analytics/flags";

const VIEWS: { view: SpaceView; label: string; icon: React.ReactNode }[] = [
    { view: "pages", label: "Pages", icon: <FileIcon size={18} /> },
    { view: "apps", label: "Apps", icon: <AppsIcon size={18} /> },
    { view: "tables", label: "Tables", icon: <TableIcon size={18} /> },
    { view: "files", label: "Files", icon: <FolderIcon size={18} /> },
];

/** What a recent item is, for its icon. */
function RecentGlyph({ tab }: { tab: Tab }) {
    if (tab.kind === "app") return <AppsIcon size={17} />;
    if (tab.kind === "group") return <GroupsIcon size={17} />;
    if (tab.kind === "table" || tab.kind === "record") return <TableIcon size={17} />;
    return <FileIcon size={17} />;
}

/** The sidebar of one teammate's space, in Space's order: its places, your
 *  chats with it, and what you had open. The teammate itself sits above it,
 *  and going to another is the rail's job. Collapsed, this column goes away
 *  and the rail is what is left. */
export function SpaceNav({ pod, activeId, recents, onPick, openChatId, onOpenChat, onWorkflows, onSettings }: {
    pod: Pod;
    openChatId: string | null;
    onOpenChat: (id: string) => void;
    activeId: string;
    recents: Tab[];
    onPick: (tabId: string) => void;
    onWorkflows: () => void;
    onSettings: () => void;
}) {
    /* Your conversations, one dense line each. Not the ones a resource
       carries (they open from that resource), and not scheduled runs, which
       are the teammate's own work rather than something you said — Home lists
       those. */
    const chats = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
        staleTime: 60_000,
    });
    const yours = unbound(chats.data).filter(chat => chat.kind.toUpperCase() !== "TASK").slice(0, 8);
    /* Questions people outside the space are waiting on you for, in any of
       its groups — the list the Groups page reads, under its key. */
    const groupsOn = useFeature("groups");
    const groups = useGroups(pod.id, false, groupsOn);
    const waiting = waitingTotal(groups.data ?? []);

    return (
        <nav className="snav" aria-label={pod.name + "’s space"}>
            <div className="snav__group" data-tour="places">
                {/* Where the space opens: whose it is, what is waiting, ways to
                    start, and the box to ask it. One of the places, so in
                    their list rather than a group of its own. */}
                <button className="side__item" title="New" aria-current={activeId === "space:home" || activeId === "conversation" ? "page" : undefined} onClick={() => onPick("space:home")}>
                    <PlusIcon size={18} /><span>New</span>
                </button>
                {VIEWS.map(item => (
                    <button key={item.view} className="side__item" title={item.label} aria-current={activeId === "space:" + item.view ? "page" : undefined} onClick={() => onPick("space:" + item.view)}>
                        {item.icon}<span>{item.label}</span>
                    </button>
                ))}
                <button className="side__item" title="Workflows" aria-current={activeId === "space:workflows" ? "page" : undefined} onClick={onWorkflows}><WorkflowIcon size={18} /><span>Workflows</span></button>
                {/* On one group's page this is the section it is in, not the page. */}
                {groupsOn && <button className="side__item" title="Groups" aria-current={activeId === "space:groups" ? "page" : activeId.startsWith("group:") ? "true" : undefined} onClick={() => onPick("space:groups")}>
                    <GroupsIcon size={18} /><span>Groups</span>
                    {waiting > 0 && <>
                        <span className="snav__count" aria-hidden="true">{waiting}</span>
                        <span className="sr-only">, {sayWaiting(waiting)}</span>
                    </>}
                </button>}
            </div>


            {yours.length > 0 && (
                <div className="snav__group">
                    <div className="snav__label">Chats</div>
                    {yours.map(chat => (
                        <button key={chat.id} className="side__item snav__chat" aria-current={activeId === "conversation" && openChatId === chat.id ? "page" : undefined} onClick={() => onOpenChat(chat.id)} title={chat.title}>
                            <span>{chat.title}</span>
                        </button>
                    ))}
                    <button className="side__item snav__chat snav__all" aria-current={activeId === "space:chats" ? "page" : undefined} onClick={() => onPick("space:chats")}>
                        <span>All chats</span>
                    </button>
                </div>
            )}

            {recents.length > 0 && (
                <div className="snav__group">
                    <div className="snav__label">Recents</div>
                    {recents.slice(0, 6).map(tab => (
                        <button key={tab.id} className="side__item" aria-current={activeId === tab.id ? "page" : undefined} onClick={() => onPick(tab.id)} title={tab.label}>
                            <RecentGlyph tab={tab} /><span>{tab.label}</span>
                        </button>
                    ))}
                </div>
            )}

            <div className="snav__group snav__foot">
                <button className="side__item" title="Settings" aria-current={activeId === "space:settings" ? "page" : undefined} onClick={onSettings}><SettingsIcon size={18} /><span>Settings</span></button>
            </div>
        </nav>
    );
}
