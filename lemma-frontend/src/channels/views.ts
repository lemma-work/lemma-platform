import type { Channel, ChannelKind, ContactStatus } from "./model";

/** The ways the Channels place cuts its list: everything you can see, the
 *  archive, and -- for whoever answers contacts -- the support inbox's views.
 *
 *  Each view is a filter the API applies (`kind`, `assignee`, `unassigned`,
 *  `status`, `needs_person`, `archived`). A contact's channel needs a person
 *  when it is open, nobody is assigned, and its agent is off or asked for
 *  one -- the server's rule, which `needsPerson` repeats for a row's chip. */

export type ChannelView = "all" | "archived" | "unassigned" | "mine" | "needs" | "contacts";

export interface ListFilter {
    kind?: ChannelKind;
    archived: boolean;
    assignee?: "me";
    unassigned?: boolean;
    status?: ContactStatus;
    needsPerson?: boolean;
}

export const CONTACT_VIEWS: readonly { view: ChannelView; label: string }[] = [
    { view: "unassigned", label: "Unassigned" },
    { view: "mine", label: "Mine" },
    { view: "needs", label: "Needs a person" },
    { view: "contacts", label: "All contacts" },
];

export const STATUS_LABELS: Record<ContactStatus, string> = {
    open: "Open",
    waiting_on_contact: "Waiting on them",
    closed: "Closed",
};

export function isContactView(view: ChannelView): boolean {
    return CONTACT_VIEWS.some((one) => one.view === view);
}

/** The status a contact view shows unless the reader picks another: the
 *  open ones for what is waiting on somebody, every status for the rest.
 *  "Needs a person" is open by definition, and takes no status. */
export function defaultStatus(view: ChannelView): ContactStatus | null {
    return view === "unassigned" ? "open" : null;
}

/** Whether a view offers a status to pick. */
export function takesStatus(view: ChannelView): boolean {
    return isContactView(view) && view !== "needs";
}

/** What the API is asked for a view. `status` is the reader's pick; `null`
 *  is any status. */
export function filterFor(view: ChannelView, status: ContactStatus | null = defaultStatus(view)): ListFilter {
    switch (view) {
        case "all": return { archived: false };
        case "archived": return { archived: true };
        case "unassigned": return { kind: "CONTACT", archived: false, unassigned: true, ...(status ? { status } : {}) };
        case "needs": return { kind: "CONTACT", archived: false, needsPerson: true };
        case "mine": return { kind: "CONTACT", archived: false, assignee: "me", ...(status ? { status } : {}) };
        case "contacts": return { kind: "CONTACT", archived: false, ...(status ? { status } : {}) };
    }
}

/** Whether a contact's channel needs a person. */
export function needsPerson(channel: Pick<Channel, "kind" | "assignee" | "status" | "agentsAnswer" | "attention">): boolean {
    return channel.kind === "CONTACT" && !channel.assignee && channel.status !== "closed" && (Boolean(channel.attention) || !channel.agentsAnswer);
}

/** What an empty view says. */
export function emptyView(view: ChannelView): string {
    switch (view) {
        case "all": return "No channels yet.";
        case "archived": return "Nothing is archived.";
        case "unassigned": return "Every contact who is waiting has somebody on it.";
        case "mine": return "No contacts are assigned to you.";
        case "needs": return "Nobody is waiting on a person.";
        case "contacts": return "No contact has written yet.";
    }
}
