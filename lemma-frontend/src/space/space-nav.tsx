"use client";

import { useQuery } from "@tanstack/react-query";
import { unbound } from "@/thread/conversation-list";
import { source, type Pod, type SpaceView, type Tab } from "@/data";
import { AppsIcon, FileIcon, FolderIcon, HomeIcon, SettingsIcon, TableIcon, WorkflowIcon } from "@/ui/icons";

const VIEWS: { view: SpaceView; label: string; icon: React.ReactNode }[] = [
    { view: "pages", label: "Pages", icon: <FileIcon size={18} /> },
    { view: "apps", label: "Apps", icon: <AppsIcon size={18} /> },
    { view: "tables", label: "Tables", icon: <TableIcon size={18} /> },
    { view: "files", label: "Files", icon: <FolderIcon size={18} /> },
];

/** What a recent item is, for its icon. */
function RecentGlyph({ tab }: { tab: Tab }) {
    if (tab.kind === "app") return <AppsIcon size={17} />;
    if (tab.kind === "table" || tab.kind === "record") return <TableIcon size={17} />;
    return <FileIcon size={17} />;
}

/** The sidebar of one space, in Space's order: make something, find
 *  something, the bots that work here, what you had open, and the other
 *  switcher above it is for going to another space. */
export function SpaceNav({ pod, activeId, recents, onPick, openChatId, onOpenChat, onWorkflows, onSettings, compact = false }: {
    pod: Pod;
    /** The collapsed sidebar: the same places as icons, and the bots as
     *  their marks. Chats and recents need their words, so they wait. */
    compact?: boolean;
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
       are a bot's work rather than something you said. */
    const chats = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
        staleTime: 60_000,
    });
    const yours = unbound(chats.data).filter(chat => chat.kind.toUpperCase() !== "TASK").slice(0, 8);

    return (
        <nav className={"snav" + (compact ? " snav--compact" : "")} aria-label="Space">
            <div className="snav__group">
                {/* Where the space opens: what is waiting, ways to start, and
                    the box to ask the space's bot. */}
                <button className="side__item" title="Home" aria-current={activeId === "space:home" || activeId === "conversation" ? "page" : undefined} onClick={() => onPick("space:home")}>
                    <HomeIcon size={18} /><span>Home</span>
                </button>
            </div>
            <div className="snav__group">
                {VIEWS.map(item => (
                    <button key={item.view} className="side__item" title={item.label} aria-current={activeId === "space:" + item.view ? "page" : undefined} onClick={() => onPick("space:" + item.view)}>
                        {item.icon}<span>{item.label}</span>
                    </button>
                ))}
                <button className="side__item" title="Workflows" aria-current={activeId === "space:workflows" ? "page" : undefined} onClick={onWorkflows}><WorkflowIcon size={18} /><span>Workflows</span></button>
            </div>


            {!compact && yours.length > 0 && (
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

            {!compact && recents.length > 0 && (
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
                <button className="side__item" title="Space settings" aria-current={activeId === "space:settings" ? "page" : undefined} onClick={onSettings}><SettingsIcon size={18} /><span>Space settings</span></button>
            </div>
        </nav>
    );
}
