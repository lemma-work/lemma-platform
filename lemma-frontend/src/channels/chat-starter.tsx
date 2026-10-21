"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { Pod } from "@/data";
import { ChannelIcon } from "@/shell/channels";
import { useSurfaces } from "@/shell/surfaces";
import { ChatIcon, GroupsIcon, PlusIcon } from "@/ui/icons";
import { mayStartChannel } from "./abilities";
export { ChannelMark } from "./chat-mark";
import { NewSharedChat } from "./new-channel";
import { GroupSheets, type GroupSheet } from "./source-sheets";
import { groupStarters, platformName, type Starter } from "./sources";
import { useAbilities } from "./use-abilities";

/** The one place to start talking: a chat with the teammate, a chat shared
 *  with people in the space, or the teammate brought into a group on a
 *  platform it is connected to. A platform it is not connected to is
 *  offered as connecting it, never as a dead option. */
export function StartChat({ pod, look, onNewChat, onOpenChannel, onConnect }: {
    pod: Pod;
    /** A "+" beside the sidebar's Chats, or a "New" pill on the Chats page. */
    look: "icon" | "pill";
    onNewChat: () => void;
    onOpenChannel: (channelId: string) => void;
    /** The space's bots, to connect or fix one. */
    onConnect: () => void;
}) {
    const [open, setOpen] = useState(false);
    /* Placed from the button, in a portal: the sidebar scrolls, and would
       cut a menu inside it off at its edge. */
    const [at, setAt] = useState<{ top: number; left?: number; right?: number } | null>(null);
    const [sheet, setSheet] = useState<GroupSheet | null>(null);
    const [making, setMaking] = useState(false);
    const menuId = useId();
    const button = useRef<HTMLButtonElement>(null);
    const menu = useRef<HTMLDivElement>(null);
    const mayShare = mayStartChannel(useAbilities(pod.id));
    const surfaces = useSurfaces(pod.id);
    const starters = useMemo(() => groupStarters(surfaces.data ?? []), [surfaces.data]);
    const teammate = pod.teammate?.name || pod.name;

    useEffect(() => {
        if (!open) { setAt(null); return; }
        const place = () => {
            const rect = button.current?.getBoundingClientRect();
            if (!rect) return;
            setAt(look === "icon"
                ? { top: rect.bottom + 6, left: Math.max(16, rect.left - 8) }
                : { top: rect.bottom + 6, right: Math.max(16, window.innerWidth - rect.right) });
        };
        place();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            if (menu.current?.contains(target) || button.current?.contains(target)) return;
            setOpen(false);
        };
        document.addEventListener("mousedown", away);
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        return () => {
            document.removeEventListener("mousedown", away);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [open, look]);
    useEffect(() => { if (at) menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus(); }, [at]);

    const keys = (event: KeyboardEvent<HTMLDivElement>) => {
        const items = Array.from(menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []);
        const at = items.indexOf(document.activeElement as HTMLElement);
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); button.current?.focus(); }
        else if (event.key === "ArrowDown") { event.preventDefault(); items[(at + 1) % items.length]?.focus(); }
        else if (event.key === "ArrowUp") { event.preventDefault(); items[(at - 1 + items.length) % items.length]?.focus(); }
        else if (event.key === "Tab") setOpen(false);
    };

    const start = (starter: Starter) => {
        setOpen(false);
        if (!starter.surface || starter.state !== "ready") { onConnect(); return; }
        if (starter.platform === "WHATSAPP") setSheet({ kind: "whatsapp", surface: starter.surface });
        else if (starter.platform === "SLACK") setSheet({ kind: "slack", surface: starter.surface });
        else setSheet({ kind: "telegram", surface: starter.surface });
    };

    const words = (starter: Starter) => {
        const name = platformName(starter.platform);
        if (starter.state === "missing") return { title: "Connect " + name + " first", note: "Then " + teammate + " can join " + (starter.platform === "SLACK" ? "channels" : "groups") + " there." };
        if (starter.state === "unwell") return { title: name + " needs attention", note: "Open its settings first." };
        if (starter.platform === "WHATSAPP") return { title: "Start a WhatsApp group", note: teammate + " creates it; you share the link." };
        if (starter.platform === "TELEGRAM") return { title: "Add " + teammate + " to a Telegram group", note: "Pick the group in Telegram." };
        return { title: "Add " + teammate + " to a Slack channel", note: "Invite it in the channel." };
    };

    return (
        <div className="chatstart" data-look={look}>
            <button
                ref={button}
                type="button"
                className={look === "icon" ? "icon-button chatstart__icon" : "all__new-button"}
                aria-label={look === "icon" ? "Start a chat" : undefined}
                title={look === "icon" ? "Start a chat" : undefined}
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                onClick={() => setOpen((was) => !was)}
            >
                <PlusIcon size={look === "icon" ? 14 : 15} />{look === "pill" && " New"}
            </button>
            {open && at && createPortal(
                <div className="chatstart__menu" data-look={look} role="menu" id={menuId} aria-label="Start a chat" ref={menu} onKeyDown={keys} style={{ top: at.top, left: at.left, right: at.right }}>
                    <button type="button" role="menuitem" className="chatstart__item" onClick={() => { setOpen(false); onNewChat(); }}>
                        <span className="chatstart__mark"><ChatIcon size={18} /></span>
                        <span className="chatstart__text">
                            <span className="chatstart__title">New chat</span>
                            <span className="chatstart__note">Just you and {teammate}.</span>
                        </span>
                    </button>
                    {mayShare && (
                        <button type="button" role="menuitem" className="chatstart__item" onClick={() => { setOpen(false); setMaking(true); }}>
                            <span className="chatstart__mark"><GroupsIcon size={18} /></span>
                            <span className="chatstart__text">
                                <span className="chatstart__title">New shared chat</span>
                                <span className="chatstart__note">With people in {pod.name}. Anyone can ask {teammate}.</span>
                            </span>
                        </button>
                    )}
                    {starters.length > 0 && <span className="chatstart__sep" role="separator" />}
                    {surfaces.isPending && <p className="chatstart__quiet" role="status">Reading the connections…</p>}
                    {starters.map((starter) => {
                        const said = words(starter);
                        return (
                            <button
                                key={starter.platform}
                                type="button"
                                role="menuitem"
                                className="chatstart__item"
                                data-ready={starter.state === "ready" || undefined}
                                onClick={() => start(starter)}
                            >
                                <span className="chatstart__mark chatstart__mark--platform"><ChannelIcon platform={starter.platform} size={18} /></span>
                                <span className="chatstart__text">
                                    <span className="chatstart__title">{said.title}</span>
                                    <span className="chatstart__note">{said.note}</span>
                                </span>
                            </button>
                        );
                    })}
                </div>,
                document.body,
            )}
            {sheet && <GroupSheets pod={pod} sheet={sheet} onClose={() => setSheet(null)} onOpenChannel={onOpenChannel} />}
            {making && <NewSharedChat pod={pod} onClose={() => setMaking(false)} onMade={(id) => { setMaking(false); onOpenChannel(id); }} />}
        </div>
    );
}
