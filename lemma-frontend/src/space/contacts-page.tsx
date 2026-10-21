"use client";

import { useRef, useState } from "react";
import { source, type Pod } from "@/data";
import {
    EMPTY_WIDGET_DRAFT,
    widgetAnswerChoices,
    contactName,
    handleText,
    outreachProblem,
    outreachSaid,
    readOrigins,
    vouchedBy,
    widgetProblem,
    writableHandles,
    type Contact,
    type ContactReach,
    type NewWebWidget,
    type WebWidget,
    type WidgetDraft,
} from "@/data/contacts";
import { channelApi } from "@/channels/api";
import { SearchIcon } from "@/ui/icons";
import { ContactQueue } from "@/channels/contact-queue";
import { ContactBots } from "./contact-bots";
import { isForbidden } from "@/session/auth-state";
import { Modal } from "@/shell/modal";
import { channelHref, goTo } from "@/shell/place-link";
import {
    useContacts,
    useCreateWidget,
    useFollowUp,
    useForgetContact,
    useMaySeeContacts,
    useAutoAcknowledge,
    useReach,
    useReachChange,
    useSetAutoAcknowledge,
    useRemoveWidget,
    useRenameContact,
    useShareableColumns,
    useWidgetChange,
    useWidgets,
} from "./contact-queries";
import { SecretSheet, WidgetPlaces } from "./widget-places";
import { ChannelIcon } from "@/shell/channels";
import { CopyButton } from "@/thread/copy-button";

type ContactsTab = "inbox" | "people" | "setup";

/** A space's contacts: the people its bots answer who are not in it.
 *
 *  Three tabs, each a thing a member does here: answer who is waiting
 *  (Inbox), find somebody (People), and decide where they can write and what
 *  they can reach (Setup). With nobody yet, it opens on Setup. */
export function ContactsPage({ pod, onConnect }: { pod: Pod; onConnect?: () => void }) {
    const [open, setOpen] = useState<Contact | null>(null);
    const maySee = useMaySeeContacts(pod.id);
    const contacts = useContacts(pod.id);
    const count = contacts.data?.length ?? null;
    const [picked, setPicked] = useState<ContactsTab | null>(null);
    const tab: ContactsTab = picked ?? (count === 0 ? "setup" : "inbox");
    if (!maySee) {
        return (
            <div className="all contacts">
                <header className="contacts__head"><h1>Contacts</h1></header>
                <p className="contacts__quiet">Only {pod.name}’s editors and admins see its contacts.</p>
            </div>
        );
    }
    const tabs: { at: ContactsTab; label: string; count?: number | null }[] = [
        { at: "inbox", label: "Inbox" },
        { at: "people", label: "People", count },
        { at: "setup", label: "Setup" },
    ];
    return (
        <div className="all contacts">
            <header className="contacts__head">
                <h1>Contacts</h1>
            </header>
            <div className="ctabs" role="tablist" aria-label="Contacts">
                {tabs.map((one) => (
                    <button key={one.at} type="button" role="tab" className="ctabs__tab" aria-selected={tab === one.at} onClick={() => setPicked(one.at)}>
                        {one.label}{one.count ? <span className="ctabs__count">{one.count}</span> : null}
                    </button>
                ))}
            </div>
            {tab === "inbox" && (count === 0
                ? <Nobody pod={pod} onSetup={() => setPicked("setup")} />
                : <ContactQueue pod={pod} bare />)}
            {tab === "people" && (count === 0
                ? <Nobody pod={pod} onSetup={() => setPicked("setup")} />
                : <People pod={pod} onOpen={setOpen} />)}
            {tab === "setup" && (
                <div className="csetup">
                    <ContactBots pod={pod} onConnect={onConnect} />
                    <Widgets pod={pod} />
                    <Acknowledge pod={pod} />
                    <Reach pod={pod} />
                </div>
            )}
            {open && <ContactSheet pod={pod} contact={open} onClose={() => setOpen(null)} />}
        </div>
    );
}

function Nobody({ pod, onSetup }: { pod: Pod; onSetup: () => void }) {
    return (
        <p className="contacts__quiet">
            Nobody outside {pod.name} has written yet. <button type="button" className="gsheet__inline" onClick={onSetup}>Set up where they can write</button>
        </p>
    );
}

/* ── people ────────────────────────────────────────────────────────── */

function People({ pod, onOpen }: { pod: Pod; onOpen: (contact: Contact) => void }) {
    const contacts = useContacts(pod.id);
    const [query, setQuery] = useState("");
    const needle = query.trim().toLowerCase();
    const shown = (contacts.data ?? []).filter((contact) => !needle || (contactName(contact) + " " + contact.handles.map(handleText).join(" ")).toLowerCase().includes(needle));
    if (contacts.isPending) return <p className="all__empty" role="status">Loading…</p>;
    if (!contacts.data) {
        return (
            <div className="contacts__trouble" role="alert">
                <p>{isForbidden(contacts.error) ? "You may not see " + pod.name + "’s contacts." : "Couldn’t load the contacts."}</p>
                {!isForbidden(contacts.error) && <button type="button" className="ghost-pill" onClick={() => void contacts.refetch()}>Try again</button>}
            </div>
        );
    }
    return (
        <section aria-label={pod.name + "’s contacts"}>
            <label className="cfind">
                <SearchIcon size={15} aria-hidden="true" />
                <span className="sr-only">Find a contact</span>
                <input type="search" value={query} placeholder="Find by name, email or phone" onChange={(event) => setQuery(event.target.value)} />
            </label>
            <div className="cpeople" role="table" aria-label="Contacts">
                <div className="cpeople__row cpeople__row--head" role="row">
                    <span role="columnheader">Name</span>
                    <span role="columnheader">Reached on</span>
                    <span role="columnheader">Added</span>
                </div>
                {shown.map((contact) => (
                    <button key={contact.id} type="button" className="cpeople__row" role="row" onClick={() => onOpen(contact)}>
                        <span role="cell" className="cpeople__name">
                            <span className="cpeople__face" aria-hidden="true">{(contactName(contact).trim()[0] ?? "?").toUpperCase()}</span>
                            <span>{contactName(contact)}</span>
                        </span>
                        <span role="cell" className="cpeople__muted">{contact.anonymous ? "Not confirmed yet" : contact.handles.map(handleText).join(" · ") || "No handle"}</span>
                        <span role="cell" className="cpeople__muted">{new Date(contact.createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
                    </button>
                ))}
                {shown.length === 0 && <p className="contacts__quiet">Nobody matches.</p>}
            </div>
        </section>
    );
}

function ContactSheet({ pod, contact, onClose }: { pod: Pod; contact: Contact; onClose: () => void }) {
    const rename = useRenameContact(pod.id);
    const forget = useForgetContact(pod.id);
    const followUp = useFollowUp(pod.id);
    const [name, setName] = useState(contact.name ?? "");
    const [message, setMessage] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [sentTo, setSentTo] = useState<string | null>(null);
    const handles = writableHandles(contact);
    const [handleAt, setHandleAt] = useState(0);
    /* The same line however often it is retried, until the text changes. */
    const lineId = useRef<string | null>(null);
    const [confirming, setConfirming] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);

    const download = async () => {
        setProblem(null);
        try {
            const held = await source.exportContact(pod.id, contact.id);
            const url = URL.createObjectURL(new Blob([JSON.stringify(held, null, 2)], { type: "application/json" }));
            const link = document.createElement("a");
            link.href = url;
            link.download = (contactName(contact).replace(/[^\w.-]+/g, "-") || "contact") + ".json";
            link.click();
            URL.revokeObjectURL(url);
        } catch (error) {
            setProblem(isForbidden(error) ? "Only an admin of " + pod.name + " can export a contact." : "Couldn’t export. Try again.");
        }
    };

    return (
        <Modal title={contactName(contact)} subtitle={"A contact of " + pod.name} onClose={onClose}>
            <div className="csheet">
                <ul className="csheet__handles" aria-label="How they are known">
                    {contact.handles.map((handle) => (
                        <li key={handle.kind + handle.value}>
                            <span>{handleText(handle)}</span>
                            <small>{vouchedBy(handle)}</small>
                        </li>
                    ))}
                </ul>

                <form
                    className="csheet__row"
                    onSubmit={(event) => {
                        event.preventDefault();
                        rename.mutate({ contactId: contact.id, name: name.trim() || null });
                    }}
                >
                    <label className="record-form__field">
                        <span className="smanage__label">Name</span>
                        <input value={name} maxLength={255} onChange={(event) => setName(event.target.value)} placeholder="How to address them" />
                    </label>
                    <button type="submit" className="ghost-pill" disabled={rename.isPending || name === (contact.name ?? "")}>
                        {rename.isPending ? "Saving…" : "Save"}
                    </button>
                </form>

                {handles.length > 0 ? (
                    <form
                        className="csheet__write"
                        onSubmit={(event) => {
                            event.preventDefault();
                            const handle = handles[handleAt] ?? handles[0];
                            if (!handle || !message.trim()) return;
                            setSaid(null);
                            followUp.mutate(
                                { contactId: contact.id, message: message.trim(), handle, clientMessageId: (lineId.current ??= crypto.randomUUID()) },
                                {
                                    onSuccess: (sent) => {
                                        lineId.current = null;
                                        setMessage("");
                                        setSentTo(sent.channelId);
                                        setSaid(outreachSaid(sent));
                                    },
                                    onError: (error) => setSaid(outreachProblem(error)),
                                },
                            );
                        }}
                    >
                        <label className="record-form__field">
                            <span className="smanage__label">Write to them first</span>
                            <textarea rows={3} value={message} maxLength={4000} onChange={(event) => { lineId.current = null; setMessage(event.target.value); }} placeholder={"From " + pod.name} />
                        </label>
                        {handles.length > 1 && (
                            <label className="record-form__field">
                                <span className="smanage__label">On</span>
                                <select value={handleAt} onChange={(event) => { lineId.current = null; setHandleAt(Number(event.target.value)); }}>
                                    {handles.map((handle, index) => <option key={handle.kind + handle.value} value={index}>{handleText(handle)}</option>)}
                                </select>
                            </label>
                        )}
                        <small className="csheet__fine">Email any time; WhatsApp only within a day of their last message there. It goes in their channel, where you can see whether it went.</small>
                        <div className="csheet__actions">
                            <button type="submit" className="pill-button" disabled={followUp.isPending || !message.trim()}>
                                {followUp.isPending ? "Sending…" : "Send"}
                            </button>
                            {said && <p role="status">{said}</p>}
                            {sentTo && <button type="button" className="linkish" onClick={() => { onClose(); goTo(channelHref(pod.id, sentTo)); }}>Open their channel</button>}
                        </div>
                    </form>
                ) : (
                    <p className="csheet__fine">There is no address or number to write to them on. They can be answered when they next write.</p>
                )}

                <OpenChannel pod={pod} contact={contact} onLeave={onClose} />

                <div className="csheet__foot">
                    <button type="button" className="linkish" onClick={() => void download()}>Export what {pod.name} holds</button>
                    {confirming ? (
                        <span className="csheet__confirm">
                            Forget them and every conversation with them?
                            <button type="button" className="ghost-pill" disabled={forget.isPending} onClick={() => forget.mutate(contact.id, {
                                onSuccess: onClose,
                                onError: (error) => setProblem(isForbidden(error) ? "Only an admin of " + pod.name + " can forget a contact." : "Couldn’t forget them. Try again."),
                            })}>{forget.isPending ? "Forgetting…" : "Forget"}</button>
                            <button type="button" className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                        </span>
                    ) : (
                        <button type="button" className="linkish" onClick={() => setConfirming(true)}>Forget this contact</button>
                    )}
                </div>
                {problem && <p role="alert">{problem}</p>}
            </div>
        </Modal>
    );
}

/** A contact's channel: everything they and the space's agents said, where
 *  an editor can step in. Found when asked for, since a contact who has
 *  never written has none. */
function OpenChannel({ pod, contact, onLeave }: { pod: Pod; contact: Contact; onLeave: () => void }) {
    const [state, setState] = useState<"idle" | "finding" | "none" | "failed">("idle");
    const open = async () => {
        setState("finding");
        try {
            const found = await channelApi(pod.id).list({ kind: "CONTACT", archived: false, contactId: contact.id, limit: 1 });
            const channel = found.items[0];
            if (!channel) { setState("none"); return; }
            setState("idle");
            onLeave();
            goTo(channelHref(pod.id, channel.id));
        } catch {
            setState("failed");
        }
    };
    return (
        <div className="csheet__actions">
            <button type="button" className="ghost-pill" disabled={state === "finding"} onClick={() => void open()}>
                {state === "finding" ? "Finding it…" : "Open their channel"}
            </button>
            {state === "none" && <p role="status">They have no channel yet: it starts when they first write.</p>}
            {state === "failed" && <p role="alert">Couldn’t find their channel. Try again.</p>}
        </div>
    );
}

/* ── acknowledging ─────────────────────────────────────────────────── */

/** The line said once to a contact who writes while their channel's agent
 *  is off, so nobody waits in silence: "Thanks -- someone from {pod} will
 *  reply." Said once per open conversation, until a member writes. */
function Acknowledge({ pod }: { pod: Pod }) {
    const current = useAutoAcknowledge(pod.id);
    const set = useSetAutoAcknowledge(pod.id);
    const [draft, setDraft] = useState<string | null>(null);
    const value = draft ?? current.data ?? "";
    const on = Boolean(current.data);
    return (
        <section className="ccard" aria-labelledby="contacts-ack">
            <div className="ccard__head"><h2 id="contacts-ack">Auto-reply</h2></div>
            <p className="ccard__sub">Sent once when someone writes and nobody is answering.</p>
            <form
                className="csheet__write"
                onSubmit={(event) => {
                    event.preventDefault();
                    set.mutate(value.trim() || null, { onSuccess: () => setDraft(null) });
                }}
            >
                <label className="record-form__field">
                    <input
                        value={value}
                        maxLength={500}
                        disabled={current.isPending}
                        placeholder={"Thanks — someone from " + pod.name + " will reply."}
                        onChange={(event) => setDraft(event.target.value)}
                    />
                </label>
                <div className="csheet__actions">
                    <button type="submit" className="ghost-pill" disabled={set.isPending || draft === null}>{set.isPending ? "Saving…" : "Save"}</button>
                    {on && draft === null && <button type="button" className="linkish" disabled={set.isPending} onClick={() => set.mutate(null)}>Turn it off</button>}
                    {set.isError && <p role="alert">{isForbidden(set.error) ? "Only an admin of " + pod.name + " can change that." : "Couldn’t save it. Try again."}</p>}
                    {current.isError && <p role="alert">Couldn’t read it.</p>}
                </div>
            </form>
        </section>
    );
}

/* ── widgets ───────────────────────────────────────────────────────── */

function Widgets({ pod }: { pod: Pod }) {
    const widgets = useWidgets(pod.id);
    const change = useWidgetChange(pod.id);
    const remove = useRemoveWidget(pod.id);
    const [making, setMaking] = useState(false);
    const [revealed, setRevealed] = useState<{ widget: WebWidget; secret: string } | null>(null);
    const [asking, setAsking] = useState<{ widget: WebWidget; to: "remove" | "rotate" } | null>(null);
    const [rotating, setRotating] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const choices = widgetAnswerChoices();
    /* The chat whose link, code and secret are showing: one at a time. */
    const [open, setOpen] = useState<string | null>(null);

    const rotate = async (widget: WebWidget) => {
        setProblem(null);
        setRotating(true);
        try {
            const secret = await source.reissueWidget(pod.id, widget.id);
            setAsking(null);
            setRevealed({ widget, secret });
        } catch {
            setAsking(null);
            setProblem("Couldn’t make a new secret. Try again.");
        } finally {
            setRotating(false);
        }
    };
    const removeIt = (widget: WebWidget) => {
        setProblem(null);
        remove.mutate(widget.id, {
            onSuccess: () => setAsking(null),
            onError: () => { setAsking(null); setProblem("Couldn’t remove it. Try again."); },
        });
    };

    return (
        <section className="ccard" aria-labelledby="contacts-widgets">
            <div className="ccard__head">
                <h2 id="contacts-widgets">Web chat</h2>
                <button type="button" className="ghost-pill" onClick={() => setMaking(true)}>Add</button>
            </div>
            {widgets.isPending ? (
                <p className="all__empty" role="status">Loading…</p>
            ) : !widgets.data ? (
                <p role="alert">Couldn’t load them. <button type="button" className="linkish" onClick={() => void widgets.refetch()}>Try again</button></p>
            ) : widgets.data.length === 0 ? (
                <p className="ccard__sub">A chat for your website, or a link to share.</p>
            ) : (
                <ul className="crows cbots">
                    {widgets.data.map((widget) => (
                        <li key={widget.id} className="crow cbot cwebchat">
                            <span className="cbot__mark" aria-hidden="true"><ChannelIcon platform="WEB" size={22} /></span>
                            <span className="cbot__text">
                                <span className="crow__name">{widget.name}</span>
                                <span className="crow__sub">{widget.allowedOrigins.length ? "On " + widget.allowedOrigins.join(", ") : "On any website"}</span>
                            </span>
                            <span className="cwidget__acts">
                                <label className="cbot__choose">
                                    <span className="sr-only">Whom {widget.name} answers</span>
                                    <select
                                        value={widget.answer}
                                        disabled={change.isPending}
                                        title={choices.find((choice) => choice.value === widget.answer)?.note}
                                        onChange={(event) => change.mutate({ widgetId: widget.id, change: { answer: event.target.value as WebWidget["answer"] } })}
                                    >
                                        {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
                                    </select>
                                </label>
                                <CopyButton text={widget.pageUrl} label={"Copy the link to " + widget.name} />
                                <button type="button" className="ghost-pill" aria-expanded={open === widget.id} onClick={() => setOpen(open === widget.id ? null : widget.id)}>
                                    {open === widget.id ? "Done" : "Set up"}
                                </button>
                            </span>
                            {open === widget.id && (
                                <div className="cwidget__more">
                                    <WidgetPlaces link={widget.pageUrl} embed={widget.embed} />
                                    <div className="cwidget__actions">
                                        <button type="button" className="linkish" onClick={() => setAsking({ widget, to: "rotate" })}>New signing secret</button>
                                        <button type="button" className="linkish" disabled={remove.isPending} onClick={() => setAsking({ widget, to: "remove" })}>Remove</button>
                                    </div>
                                </div>
                            )}
                        </li>
                    ))}
                </ul>
            )}
            {problem && <p role="alert">{problem}</p>}
            {making && (
                <WidgetSheet
                    pod={pod}
                    onClose={() => setMaking(false)}
                    onMade={(made) => { setMaking(false); setRevealed({ widget: made, secret: made.signingSecret }); }}
                />
            )}
            {asking?.to === "remove" && (
                <ConfirmSheet
                    title={"Remove " + asking.widget.name + "?"}
                    says="Pages with its code or link stop showing the chat, and anyone talking to it now is cut off. A new chat gets a new key."
                    act="Remove it"
                    busy={remove.isPending}
                    onCancel={() => setAsking(null)}
                    onConfirm={() => removeIt(asking.widget)}
                />
            )}
            {asking?.to === "rotate" && (
                <ConfirmSheet
                    title={"A new secret for " + asking.widget.name + "?"}
                    says="The old one stops working at once: your site’s server has to sign with the new one, and customers it signed in with the old one start over."
                    act="Make a new secret"
                    busy={rotating}
                    onCancel={() => setAsking(null)}
                    onConfirm={() => void rotate(asking.widget)}
                />
            )}
            {revealed && <SecretSheet widget={revealed.widget} secret={revealed.secret} onClose={() => setRevealed(null)} />}
        </section>
    );
}

/** Asks before something a site depends on changes under it, and says what breaks. */
function ConfirmSheet({ title, says, act, busy, onCancel, onConfirm }: {
    title: string;
    says: string;
    act: string;
    busy: boolean;
    onCancel: () => void;
    onConfirm: () => void;
}) {
    return (
        <Modal title={title} narrow onClose={() => { if (!busy) onCancel(); }}>
            <div className="csheet">
                <p>{says}</p>
                <div className="csheet__actions">
                    <button type="button" className="pill-button" disabled={busy} onClick={onConfirm}>{busy ? "Working…" : act}</button>
                    <button type="button" className="ghost-pill" disabled={busy} onClick={onCancel}>Keep it</button>
                </div>
            </div>
        </Modal>
    );
}

function WidgetSheet({ pod, onClose, onMade }: { pod: Pod; onClose: () => void; onMade: (made: NewWebWidget) => void }) {
    const create = useCreateWidget(pod.id);
    const [draft, setDraft] = useState<WidgetDraft>(EMPTY_WIDGET_DRAFT);
    const [problem, setProblem] = useState<string | null>(null);
    const set = (change: Partial<WidgetDraft>) => setDraft((was) => ({ ...was, ...change }));

    return (
        <Modal title="New web chat" subtitle={"Answered by " + pod.name} onClose={onClose}>
            <form
                className="record-form csheet"
                onSubmit={(event) => {
                    event.preventDefault();
                    const wrong = widgetProblem(draft);
                    setProblem(wrong);
                    if (wrong) return;
                    create.mutate(draft, { onSuccess: onMade, onError: () => setProblem("Couldn’t make it. Try again.") });
                }}
            >
                <fieldset disabled={create.isPending}>
                    <label className="record-form__field">
                        <span className="smanage__label">Name</span>
                        <input value={draft.name} maxLength={255} onChange={(event) => set({ name: event.target.value })} placeholder="Shop chat" />
                    </label>
                    <label className="record-form__field">
                        <span className="smanage__label">Who can chat</span>
                        <select value={draft.answer} onChange={(event) => set({ answer: event.target.value as WidgetDraft["answer"] })}>
                            {widgetAnswerChoices().filter((choice) => choice.value !== "off").map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
                        </select>
                    </label>
                    <label className="record-form__field">
                        <span className="smanage__label">Websites it may be embedded on</span>
                        <textarea rows={2} value={draft.origins} onChange={(event) => set({ origins: event.target.value })} placeholder="https://shop.example" />
                        <small>{readOrigins(draft.origins).length ? "One per line." : "Leave empty for any website. The link works either way."}</small>
                    </label>
                </fieldset>
                {problem && <p role="alert">{problem}</p>}
                <div className="csheet__actions">
                    <button type="submit" className="pill-button" disabled={create.isPending}>{create.isPending ? "Making…" : "Make the chat"}</button>
                    <button type="button" className="ghost-pill" onClick={onClose}>Cancel</button>
                </div>
            </form>
        </Modal>
    );
}

/* ── reach ─────────────────────────────────────────────────────────── */

function Reach({ pod }: { pod: Pod }) {
    const reach = useReach(pod.id);
    const change = useReachChange(pod.id);
    const [problem, setProblem] = useState<string | null>(null);
    const [picking, setPicking] = useState<string | null>(null);
    const refused = (error: unknown) =>
        setProblem(isForbidden(error) ? "Only an editor of " + pod.name + " can change that." : "Couldn’t change it. Try again.");
    const share = (table: string, columns: string[]) =>
        change.mutate({ table, on: true, columns }, { onError: refused, onSuccess: () => setPicking(null) });
    const flip = (table: string, on: boolean) => {
        if (on) setPicking(table);
        else if (picking === table) setPicking(null);
        else change.mutate({ table, on: false }, { onError: refused });
    };

    return (
        <section className="ccard ccard--wide" aria-labelledby="contacts-reach">
            <div className="ccard__head"><h2 id="contacts-reach">Shared with contacts</h2></div>
            <p className="ccard__sub">Tables and functions contacts can use, besides what is Public. Each contact sees only their own rows.</p>
            {reach.isPending ? (
                <p className="all__empty" role="status">Loading…</p>
            ) : !reach.data ? (
                <p role="alert">Couldn’t load it. <button type="button" className="linkish" onClick={() => void reach.refetch()}>Try again</button></p>
            ) : (
                <div className="creach">
                    <div>
                        <h3>Tables</h3>
                        {reach.data.tables.length === 0 ? <p className="ccard__sub">No tables yet.</p> : <Shortlist items={reach.data.tables} on={(table) => table.contactOwned} noun={["table", "tables"]} render={(table) => (
                            <div key={table.name}>
                                <label className="smanage__check">
                                    <input
                                        type="checkbox"
                                        role="switch"
                                        checked={table.contactOwned || picking === table.name}
                                        disabled={change.isPending || table.perPerson}
                                        onChange={(event) => flip(table.name, event.target.checked)}
                                    />
                                    <span>
                                        <span className="smanage__label">{table.name}</span>
                                        {reachNote(table) && <small>{reachNote(table)}</small>}
                                    </span>
                                </label>
                                {picking === table.name && (
                                    <ContactColumns
                                        podId={pod.id}
                                        table={table.name}
                                        saving={change.isPending}
                                        onShare={(columns) => share(table.name, columns)}
                                        onCancel={() => setPicking(null)}
                                    />
                                )}
                            </div>
                        )} />}
                    </div>
                    <div>
                        <h3>Functions</h3>
                        {reach.data.functions.length === 0 ? <p className="ccard__sub">No functions yet.</p> : <Shortlist items={reach.data.functions} on={(fn) => fn.contactsInvoke} noun={["function", "functions"]} render={(fn) => (
                            <label key={fn.name} className="smanage__check">
                                <input
                                    type="checkbox"
                                    role="switch"
                                    checked={fn.contactsInvoke}
                                    disabled={change.isPending}
                                    onChange={(event) => change.mutate({ fn: fn.name, on: event.target.checked }, { onError: refused })}
                                />
                                <span>
                                    <span className="smanage__label">{fn.name}</span>
                                    {fn.description && <small className="ccard__clip" title={fn.description}>{fn.description}</small>}
                                </span>
                            </label>
                        )} />}
                    </div>
                </div>
            )}
            {problem && <p role="alert">{problem}</p>}
        </section>
    );
}

/** A long list cut short: what is on first, then a few more, the rest one
 *  click away. The order is fixed when it first shows, so ticking a box
 *  does not move the row out from under the pointer. */
function Shortlist<T extends { name: string }>({ items, on, noun, render }: {
    items: T[];
    on: (item: T) => boolean;
    noun: [string, string];
    render: (item: T) => React.ReactNode;
}) {
    const [order] = useState(() => [...items.filter(on), ...items.filter((item) => !on(item))].map((item) => item.name));
    const [all, setAll] = useState(false);
    const rank = (item: T) => { const at = order.indexOf(item.name); return at === -1 ? order.length : at; };
    const sorted = [...items].sort((a, b) => rank(a) - rank(b));
    const keep = Math.max(SHORTLIST, items.filter(on).length);
    const shown = all ? sorted : sorted.slice(0, keep);
    const more = sorted.length - keep;
    return (
        <>
            {shown.map(render)}
            {sorted.length > keep && (
                <button type="button" className="linkish creach__more" onClick={() => setAll(!all)}>
                    {all ? "Show fewer" : "Show " + more + " more " + (more === 1 ? noun[0] : noun[1])}
                </button>
            )}
        </>
    );
}

/** How many of a long list show before "Show more". */
const SHORTLIST = 5;

function reachNote(table: ContactReach["tables"][number]): string {
    if (table.perPerson) return "Per member: can’t be shared";
    if (!table.contactOwned) return "";
    return "Shows " + table.contactColumns.join(", ");
}

/** The columns a contact may read of their own rows. The member's explicit
 *  choice, so a column added to the table later stays members-only. */
function ContactColumns({
    podId,
    table,
    saving,
    onShare,
    onCancel,
}: {
    podId: string;
    table: string;
    saving: boolean;
    onShare: (columns: string[]) => void;
    onCancel: () => void;
}) {
    const columns = useShareableColumns(podId, table);
    const [chosen, setChosen] = useState<string[]>([]);
    const toggle = (name: string, on: boolean) => setChosen((now) => (on ? [...now, name] : now.filter((entry) => entry !== name)));

    if (columns.isPending) return <p className="all__empty" role="status">Loading columns…</p>;
    if (!columns.data) return <p role="alert">Couldn’t load the columns.</p>;
    const names = columns.data;
    return (
        <fieldset className="contacts__quiet">
            <legend>What a contact may see of their own rows</legend>
            {names.map((name) => (
                <label key={name} className="smanage__check">
                    <input type="checkbox" checked={chosen.includes(name)} onChange={(event) => toggle(name, event.target.checked)} />
                    <span className="smanage__label">{name}</span>
                </label>
            ))}
            <div className="csheet__actions">
                <button type="button" className="pill-button" disabled={saving || chosen.length === 0} onClick={() => onShare(names.filter((name) => chosen.includes(name)))}>
                    Share these columns
                </button>
                <button type="button" className="linkish" onClick={onCancel}>Cancel</button>
            </div>
        </fieldset>
    );
}
