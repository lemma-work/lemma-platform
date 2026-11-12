"use client";

import type { Pod } from "@/data";
import { WaitingBell } from "@/channels/waiting-panel";
import { useMaySeeContacts } from "@/space/contact-queries";
import type { FrameActions } from "./actions";
import { outcomeView, type Go, type OutcomeTab } from "./model";
import { Icon, to, type IconName } from "./parts";

export type LemmaSection = Exclude<OutcomeTab, "home">;

/** A Lemma's pages. Its row of tabs holds what is in it, each named for the
 *  tool it stands in for -- a chat app, an app, a base, a doc app, a drive --
 *  and then About, which says what the Lemma is. The rest are parts of
 *  About: reached from it, and each still a page with an address of its own. */
export const LEMMA_SECTIONS: readonly { tab: LemmaSection; label: string; icon: IconName }[] = [
    { tab: "chats", label: "Chats", icon: "chat" },
    { tab: "apps", label: "Apps", icon: "app" },
    { tab: "tables", label: "Tables", icon: "table" },
    { tab: "pages", label: "Pages", icon: "page" },
    { tab: "files", label: "Files", icon: "file" },
    { tab: "about", label: "About", icon: "about" },
    { tab: "people", label: "People", icon: "people" },
    { tab: "contacts", label: "Contacts", icon: "inbox" },
    { tab: "automations", label: "Automations", icon: "flow" },
    { tab: "plan", label: "Plan", icon: "skill" },
    { tab: "settings", label: "Settings", icon: "gear" },
];


export function sectionLabel(tab: LemmaSection): string {
    return LEMMA_SECTIONS.find((one) => one.tab === tab)?.label ?? "";
}

/** The sections this reader has: Contacts only for whoever looks after them. */
export function useLemmaSections(podId: string) {
    const contacts = useMaySeeContacts(podId);
    return LEMMA_SECTIONS.filter((one) => one.tab !== "contacts" || contacts);
}

/** Close the menu a click inside it came from. */
export function closeMenu(event: { currentTarget: Element }) {
    event.currentTarget.closest("details")?.removeAttribute("open");
}

/** The last step of the crumb on a page drawn under the Lemma's own bar
 *  (Settings): which section this is, and a menu of the others. */
export function SectionSwitch({ pod, here, go }: { pod: Pod; here: LemmaSection; go: Go }) {
    const sections = useLemmaSections(pod.id);
    return (
        <details className="ox-more ox-switch">
            <summary aria-label={`${sectionLabel(here)}, one of ${pod.name}’s sections`}>
                {sectionLabel(here)}
                <Icon name="chevron" size={13} />
            </summary>
            <div className="ox-more__menu" role="menu">
                {sections.map((one) => {
                    const link = to(outcomeView(pod.id, one.tab), go);
                    return (
                        <a key={one.tab} role="menuitem" aria-current={one.tab === here ? "page" : undefined} href={link.href} onClick={(event) => { closeMenu(event); link.onClick(event); }}>
                            <Icon name={one.icon} size={15} />
                            {one.label}
                        </a>
                    );
                })}
            </div>
        </details>
    );
}

/** What every page of a Lemma offers at its top right: its inbox, a new
 *  chat with it, and the rest -- who has access, where people reach it,
 *  its plan, sharing it, its computer, its settings -- in a menu. */
export function LemmaActions({ pod, go, actions, newChat = true }: {
    pod: Pod;
    go: Go;
    actions: FrameActions;
    /** The way to its page, where a chat with it starts -- left out on that
     *  page itself. */
    newChat?: boolean;
}) {
    const link = (view: Parameters<typeof to>[0]) => {
        const at = to(view, go);
        return { href: at.href, onClick: (event: Parameters<typeof at.onClick>[0]) => { closeMenu(event); at.onClick(event); } };
    };
    return (
        <>
            {/* Its inbox, as the workspace has it over a pod: the reader's
                private line with it, what waits on them, and mentions. The
                sidebar holds every Lemma's inbox topic, so it is not held
                twice. */}
            <WaitingBell podId={pod.id} live={false} />
            <details className="ox-more">
                <summary aria-label={`More for ${pod.name}`} title="More"><Icon name="more" size={16} /></summary>
                <div className="ox-more__menu" role="menu">
                    <a role="menuitem" {...link(outcomeView(pod.id, "about"))}>About {pod.name}</a>
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.addPeople(pod.id); }}>Who has access</button>
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.connect(pod.id); }}>Where to reach it</button>
                    <a role="menuitem" {...link(outcomeView(pod.id, "plan"))}>Its plan</a>
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.share(pod.id); }}>Share or export</button>
                    <a role="menuitem" {...link({ name: "computer", podId: pod.id, conversationId: null })}>Its computer</a>
                    <a role="menuitem" {...link(outcomeView(pod.id, "settings"))}>Settings</a>
                </div>
            </details>
            {newChat && <a className="ox-btn ox-btn--primary ox-btn--small" {...to(outcomeView(pod.id), go)}>New chat</a>}
        </>
    );
}
