"use client";

import type { Pod } from "@/data";
import { useMaySeeContacts } from "@/space/contact-queries";
import type { FrameActions } from "./actions";
import { classic } from "./data";
import { NEW_CHAT, chatView, outcomeView, type Go, type OutcomeTab } from "./model";
import { Icon, to, type IconName } from "./parts";

export type LemmaSection = Exclude<OutcomeTab, "home">;

/** A Lemma's sections, each a page of its own under its summary. They were
 *  tabs under a tall header, and each one drew its own title and its own
 *  tabs again under those: three rows of navigation before anything to read.
 *  Now the summary lists them, and a section's page names itself once, in
 *  the bar every page of a Lemma shares. */
export const LEMMA_SECTIONS: readonly { tab: LemmaSection; label: string; icon: IconName }[] = [
    { tab: "plan", label: "Plan", icon: "skill" },
    { tab: "apps", label: "Apps", icon: "app" },
    { tab: "made", label: "Files", icon: "file" },
    { tab: "contacts", label: "Contacts", icon: "people" },
    { tab: "automations", label: "Automations", icon: "flow" },
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

/** The last step of the crumb on a section's page: which section this is,
 *  and a menu of the others, so moving between them is one click rather than
 *  a trip back through the summary. */
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

/** What every page of a Lemma offers at its top right: a new chat with it,
 *  and the rest -- who has access, where people reach it, sharing it -- in a
 *  menu. */
export function LemmaActions({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    return (
        <>
            <details className="ox-more">
                <summary aria-label={`More for ${pod.name}`} title="More"><Icon name="more" size={16} /></summary>
                <div className="ox-more__menu" role="menu">
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.addPeople(pod.id); }}>Who has access</button>
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.connect(pod.id); }}>Where people reach it</button>
                    <button type="button" role="menuitem" onClick={(event) => { closeMenu(event); actions.share(pod.id); }}>Share or export</button>
                    <a role="menuitem" href={classic.pod(pod.id)}>Open in the workspace</a>
                </div>
            </details>
            <a className="ox-btn ox-btn--primary ox-btn--small" {...to(chatView({ kind: "chat", podId: pod.id, id: NEW_CHAT }), go)}>New chat</a>
        </>
    );
}
