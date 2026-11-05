"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { unbound } from "@/thread/conversation-list";
import { source, type Pod, type SpaceView, type Tab } from "@/data";
import { AppsIcon, ContactsIcon, FileIcon, FolderIcon, PlusIcon, TableIcon, WorkflowIcon } from "@/ui/icons";
import { badge, sayUnread } from "@/channels/model";
import { chatRows } from "@/channels/chat-list";
import { ChannelMark, StartChat } from "@/channels/chat-starter";
import { allChannels, useChannelList } from "@/channels/queries";
import { useMaySeeContacts } from "./contact-queries";

const VIEWS: { view: SpaceView; label: string; icon: React.ReactNode }[] = [
    { view: "pages", label: "Pages", icon: <FileIcon size={18} /> },
    { view: "apps", label: "Apps", icon: <AppsIcon size={18} /> },
    { view: "tables", label: "Tables", icon: <TableIcon size={18} /> },
    { view: "files", label: "Files", icon: <FolderIcon size={18} /> },
];

/** How many chats the sidebar shows before "All chats". */
const SHOWN_CHATS = 10;

/** What a recent item is, for its icon. */
function RecentGlyph({ tab }: { tab: Tab }) {
    if (tab.kind === "app") return <AppsIcon size={17} />;
    if (tab.kind === "table" || tab.kind === "record") return <TableIcon size={17} />;
    return <FileIcon size={17} />;
}

/** Whether the Contacts place is shown: always, to whoever may look after
 *  contacts -- a pod's editors and admins. Empty, it is where they learn how
 *  people outside come to write, and switch it on. */
export function useContactsPlace(podId: string): boolean {
    return useMaySeeContacts(podId);
}

/** The sidebar of one teammate's space, in Space's order: its places, every
 *  chat you have with it, and what you had open. One list of chats, however
 *  they reach you: a chat here, your private line on WhatsApp or Telegram,
 *  a chat shared with people, a group it is in. The teammate itself sits
 *  above it, and going to another is the rail's job. Collapsed, this column
 *  goes away and the rail is what is left. */
export function SpaceNav({ pod, activeId, recents, onPick, openChatId, onOpenChat, onOpenChannel, onWorkflows, onConnect }: {
    pod: Pod;
    openChatId: string | null;
    onOpenChat: (id: string) => void;
    /** A chat that lives on a channel, opened -- and given a tab when it has
     *  none yet. */
    onOpenChannel: (channel: { id: string; title: string | null }) => void;
    activeId: string;
    recents: Tab[];
    onPick: (tabId: string) => void;
    onWorkflows: () => void;
    /** The space's bots, to connect one before bringing it into a group. */
    onConnect: () => void;
}) {
    /* Your conversations, one dense line each. Not the ones a resource
       carries (they open from that resource), and not scheduled runs, which
       are the teammate's own work rather than something you said -- Home
       lists those. */
    const conversations = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
        staleTime: 60_000,
    });
    const channelList = useChannelList(pod.id, { archived: false });
    const rows = useMemo(() => {
        const chats = unbound(conversations.data).filter((chat) => chat.kind.toUpperCase() !== "TASK");
        return chatRows(chats, allChannels(channelList.data), pod.name).slice(0, SHOWN_CHATS);
    }, [conversations.data, channelList.data, pod.name]);
    const contactsOn = useContactsPlace(pod.id);
    const places = recents.filter((tab) => tab.kind !== "channel");

    return (
        <nav className="snav" aria-label={pod.name + "’s space"}>
            <div className="snav__group" data-tour="places">
                {/* Where the space opens: whose it is, what is waiting, ways to
                    start, and the box to ask it. One of the places, so in
                    their list rather than a group of its own. */}
                <button className="side__item" title="New" aria-current={activeId === "space:home" || (activeId === "conversation" && !openChatId) ? "page" : undefined} onClick={() => onPick("space:home")}>
                    <PlusIcon size={18} /><span>New</span>
                </button>
                {VIEWS.map(item => (
                    <button key={item.view} className="side__item" title={item.label} aria-current={activeId === "space:" + item.view ? "page" : undefined} onClick={() => onPick("space:" + item.view)}>
                        {item.icon}<span>{item.label}</span>
                    </button>
                ))}
                <button className="side__item" title="Workflows" aria-current={activeId === "space:workflows" ? "page" : undefined} onClick={onWorkflows}><WorkflowIcon size={18} /><span>Workflows</span></button>
                {contactsOn && <button className="side__item" title="Contacts" aria-current={activeId === "space:contacts" ? "page" : undefined} onClick={() => onPick("space:contacts")}>
                    <ContactsIcon size={18} /><span>Contacts</span>
                </button>}
            </div>

            <div className="snav__group" data-tour="chats">
                <div className="snav__label snav__label--acts">
                    <span>Chats</span>
                    <StartChat
                        pod={pod}
                        look="icon"
                        onNewChat={() => onPick("space:home")}
                        onOpenChannel={(id) => onOpenChannel({ id, title: null })}
                        onConnect={onConnect}
                    />
                </div>
                {rows.map((row) => {
                    if (row.kind === "chat") {
                        return (
                            <button key={row.id} className="side__item snav__chat" aria-current={activeId === "conversation" && openChatId === row.id ? "page" : undefined} onClick={() => onOpenChat(row.id)} title={row.title}>
                                <span>{row.title}</span>
                                {row.chat.waitingOnYou && <span className="snav__dot" aria-label="needs you" />}
                            </button>
                        );
                    }
                    const channel = row.channel;
                    const count = badge(channel.unread.lines);
                    const said = sayUnread(channel.unread);
                    const calls = channel.unread.waiting > 0 || channel.unread.mentions > 0;
                    return (
                        <button
                            key={row.id}
                            className="side__item snav__chat snav__shared"
                            data-unread={channel.unread.lines > 0 || undefined}
                            aria-current={activeId === row.id ? "page" : undefined}
                            onClick={() => onOpenChannel({ id: channel.id, title: row.title })}
                            title={row.title}
                            aria-label={said ? row.title + ", " + said : row.title}
                        >
                            <span className="snav__glyph" aria-hidden="true"><ChannelMark channel={channel} /></span>
                            <span>{row.title}</span>
                            {count && <span className="snav__count" data-tone={calls ? (channel.unread.waiting ? "waiting" : "mention") : "new"} aria-hidden="true">{count}</span>}
                        </button>
                    );
                })}
                <button className="side__item snav__chat snav__all" aria-current={activeId === "space:chats" || activeId === "space:channels" ? "page" : undefined} onClick={() => onPick("space:chats")}>
                    <span>All chats</span>
                </button>
            </div>

            {places.length > 0 && (
                <div className="snav__group">
                    <div className="snav__label">Recents</div>
                    {places.slice(0, 6).map(tab => (
                        <button key={tab.id} className="side__item" aria-current={activeId === tab.id ? "page" : undefined} onClick={() => onPick(tab.id)} title={tab.label}>
                            <RecentGlyph tab={tab} /><span>{tab.label}</span>
                        </button>
                    ))}
                </div>
            )}
        </nav>
    );
}
