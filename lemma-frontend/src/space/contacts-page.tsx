"use client";

import { useMemo, useState } from "react";
import { source, type Pod } from "@/data";
import {
    answerChoices,
    contactName,
    contactSubline,
    followUpRefusal,
    formSnippet,
    handleText,
    readOrigins,
    vouchedBy,
    widgetProblem,
    type Contact,
    type NewWebWidget,
    type WebWidget,
    type WidgetDraft,
} from "@/data/contacts";
import { isForbidden } from "@/session/auth-state";
import { Modal } from "@/shell/modal";
import { CopyButton } from "@/thread/copy-button";
import {
    useContacts,
    useCreateWidget,
    useFollowUp,
    useForgetContact,
    useReach,
    useReachChange,
    useRemoveWidget,
    useRenameContact,
    useWidgetChange,
    useWidgets,
} from "./contact-queries";

/** A space's contacts: the people its bots answer who are not in it.
 *
 *  Three parts, each a thing a member does here: see who has written and
 *  write back; put a chat or a form on a website; and choose what contacts
 *  can reach beyond what is Public — their own rows of a table, and the
 *  functions opened to them. */
export function ContactsPage({ pod }: { pod: Pod }) {
    const [open, setOpen] = useState<Contact | null>(null);
    return (
        <div className="all contacts">
            <header className="contacts__head">
                <h1>Contacts</h1>
                <p>People outside {pod.name} who write to its bots or its website. They never sign in, and they see only what you let them.</p>
            </header>
            <People pod={pod} onOpen={setOpen} />
            <Widgets pod={pod} />
            <Reach pod={pod} />
            {open && <ContactSheet pod={pod} contact={open} onClose={() => setOpen(null)} />}
        </div>
    );
}

/* ── people ────────────────────────────────────────────────────────── */

function People({ pod, onOpen }: { pod: Pod; onOpen: (contact: Contact) => void }) {
    const contacts = useContacts(pod.id);
    return (
        <section className="contacts__section" aria-labelledby="contacts-people">
            <h2 id="contacts-people">People</h2>
            {contacts.isPending ? (
                <p className="all__empty" role="status">Loading contacts…</p>
            ) : !contacts.data ? (
                <div className="contacts__trouble" role="alert">
                    <p>{isForbidden(contacts.error) ? "You may not see " + pod.name + "’s contacts." : "Couldn’t load the contacts."}</p>
                    {!isForbidden(contacts.error) && <button type="button" className="ghost-pill" onClick={() => void contacts.refetch()}>Try again</button>}
                </div>
            ) : contacts.data.length === 0 ? (
                <p className="contacts__quiet">
                    Nobody yet. When someone outside {pod.name} writes to a bot that answers contacts, or to a chat on your website, they appear here.
                    Turn it on in a bot’s settings, or add a chat below.
                </p>
            ) : (
                <ul className="crows" aria-label={pod.name + "’s contacts"}>
                    {contacts.data.map((contact) => (
                        <li key={contact.id} className="crow">
                            <button type="button" className="crow__open" onClick={() => onOpen(contact)}>
                                <span className="crow__name">{contactName(contact)}</span>
                                <span className="crow__sub">{contactSubline(contact)}</span>
                            </button>
                            <span className="crow__when">{new Date(contact.createdAt).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
                        </li>
                    ))}
                </ul>
            )}
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

                <form
                    className="csheet__write"
                    onSubmit={(event) => {
                        event.preventDefault();
                        setSaid(null);
                        followUp.mutate(
                            { contactId: contact.id, message: message.trim() },
                            {
                                onSuccess: (sent) => {
                                    setMessage("");
                                    setSaid(sent.delivered ? "Sent." : "Left in their chat for their next visit.");
                                },
                                onError: (error) => setSaid(followUpRefusal((error as { code?: string }).code)),
                            },
                        );
                    }}
                >
                    <label className="record-form__field">
                        <span className="smanage__label">Write to them</span>
                        <textarea rows={3} value={message} maxLength={4000} onChange={(event) => setMessage(event.target.value)} placeholder={"From " + pod.name + ", in their latest conversation"} />
                    </label>
                    <div className="csheet__actions">
                        <button type="submit" className="pill-button" disabled={followUp.isPending || !message.trim()}>
                            {followUp.isPending ? "Sending…" : "Send"}
                        </button>
                        {said && <p role="status">{said}</p>}
                    </div>
                </form>

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

/* ── widgets ───────────────────────────────────────────────────────── */

function Widgets({ pod }: { pod: Pod }) {
    const widgets = useWidgets(pod.id);
    const change = useWidgetChange(pod.id);
    const remove = useRemoveWidget(pod.id);
    const [making, setMaking] = useState(false);
    const [revealed, setRevealed] = useState<{ widget: WebWidget; secret: string } | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const choices = useMemo(() => answerChoices(pod.name), [pod.name]);

    const rotate = async (widget: WebWidget) => {
        setProblem(null);
        try {
            setRevealed({ widget, secret: await source.rotateWidgetSecret(pod.id, widget.id) });
        } catch {
            setProblem("Couldn’t make a new secret. Try again.");
        }
    };

    return (
        <section className="contacts__section" aria-labelledby="contacts-widgets">
            <div className="contacts__sectionhead">
                <h2 id="contacts-widgets">On your website</h2>
                <button type="button" className="ghost-pill" onClick={() => setMaking(true)}>New chat or form</button>
            </div>
            {widgets.isPending ? (
                <p className="all__empty" role="status">Loading…</p>
            ) : !widgets.data ? (
                <p role="alert">Couldn’t load them. <button type="button" className="linkish" onClick={() => void widgets.refetch()}>Try again</button></p>
            ) : widgets.data.length === 0 ? (
                <p className="contacts__quiet">Put a chat bubble or a form on any website. Visitors are answered from what {pod.name} made Public until they prove who they are.</p>
            ) : (
                <ul className="cwidgets">
                    {widgets.data.map((widget) => (
                        <li key={widget.id} className="cwidget">
                            <div className="cwidget__top">
                                <span className="cwidget__name">{widget.name}</span>
                                <span className="cwidget__kind">{widget.kind === "form" ? "Form · runs " + widget.formFunction : "Chat"}</span>
                            </div>
                            <label className="record-form__field">
                                <span className="smanage__label">Answers</span>
                                <select
                                    value={widget.answer}
                                    disabled={change.isPending}
                                    onChange={(event) => change.mutate({ widgetId: widget.id, change: { answer: event.target.value as WebWidget["answer"] } })}
                                >
                                    {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.value === "off" ? "Nobody (switched off)" : choice.label}</option>)}
                                </select>
                            </label>
                            <p className="cwidget__origins">
                                {widget.allowedOrigins.length ? "Only on " + widget.allowedOrigins.join(", ") : "On any website"}
                            </p>
                            <div className="cwidget__embed">
                                <code>{widget.kind === "form" ? formSnippet(widget) : widget.embed}</code>
                                <CopyButton text={widget.kind === "form" ? formSnippet(widget) : widget.embed} label="Copy the code" />
                            </div>
                            <div className="cwidget__actions">
                                <button type="button" className="linkish" onClick={() => void rotate(widget)}>New signing secret</button>
                                <button type="button" className="linkish" disabled={remove.isPending} onClick={() => remove.mutate(widget.id)}>Remove</button>
                            </div>
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
            {revealed && <SecretSheet widget={revealed.widget} secret={revealed.secret} onClose={() => setRevealed(null)} />}
        </section>
    );
}

function WidgetSheet({ pod, onClose, onMade }: { pod: Pod; onClose: () => void; onMade: (made: NewWebWidget) => void }) {
    const create = useCreateWidget(pod.id);
    const reach = useReach(pod.id);
    const [draft, setDraft] = useState<WidgetDraft>({ name: "", kind: "chat", origins: "", answer: "anyone", formFunction: "", formRequiresCode: false });
    const [problem, setProblem] = useState<string | null>(null);
    const opened = (reach.data?.functions ?? []).filter((fn) => fn.contactsInvoke);
    const set = (change: Partial<WidgetDraft>) => setDraft((was) => ({ ...was, ...change }));

    return (
        <Modal title="New chat or form" subtitle={"Answered by " + pod.name} onClose={onClose}>
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
                    <div className="theme__modes" role="radiogroup" aria-label="Kind">
                        {(["chat", "form"] as const).map((kind) => (
                            <label key={kind} className="cradio">
                                <input type="radio" name="kind" checked={draft.kind === kind} onChange={() => set({ kind })} />
                                <span>{kind === "chat" ? "A chat bubble" : "A form"}</span>
                            </label>
                        ))}
                    </div>
                    {draft.kind === "form" && (
                        <>
                            <label className="record-form__field">
                                <span className="smanage__label">Runs</span>
                                <select value={draft.formFunction} onChange={(event) => set({ formFunction: event.target.value })}>
                                    <option value="">Choose a function</option>
                                    {opened.map((fn) => <option key={fn.name} value={fn.name}>{fn.name}</option>)}
                                </select>
                                <small>{opened.length ? "Only functions opened to contacts, below." : "Open a function to contacts first, below."}</small>
                            </label>
                            <label className="smanage__check">
                                <input type="checkbox" role="switch" checked={draft.formRequiresCode} onChange={(event) => set({ formRequiresCode: event.target.checked })} />
                                <span><span className="smanage__label">Confirm their email first</span><small>For anything you will reply to.</small></span>
                            </label>
                        </>
                    )}
                    <label className="record-form__field">
                        <span className="smanage__label">Answers</span>
                        <select value={draft.answer} onChange={(event) => set({ answer: event.target.value as WidgetDraft["answer"] })}>
                            {answerChoices(pod.name).filter((choice) => choice.value !== "off").map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
                        </select>
                    </label>
                    <label className="record-form__field">
                        <span className="smanage__label">Websites it may run on</span>
                        <textarea rows={2} value={draft.origins} onChange={(event) => set({ origins: event.target.value })} placeholder="https://shop.example" />
                        <small>{readOrigins(draft.origins).length ? "One per line." : "Leave empty to allow any website."}</small>
                    </label>
                </fieldset>
                {problem && <p role="alert">{problem}</p>}
                <div className="csheet__actions">
                    <button type="submit" className="pill-button" disabled={create.isPending}>{create.isPending ? "Making…" : "Make it"}</button>
                    <button type="button" className="ghost-pill" onClick={onClose}>Cancel</button>
                </div>
            </form>
        </Modal>
    );
}

function SecretSheet({ widget, secret, onClose }: { widget: WebWidget; secret: string; onClose: () => void }) {
    return (
        <Modal title={widget.name} subtitle="Copy the signing secret now" onClose={onClose}>
            <div className="csheet">
                <p>
                    Your site’s server uses this to sign in its own customers, so they are answered as contacts. It is shown only now; keep it off web pages.
                </p>
                <div className="cwidget__embed">
                    <code>{secret}</code>
                    <CopyButton text={secret} label="Copy the secret" />
                </div>
                <p>Then put this on the page:</p>
                <div className="cwidget__embed">
                    <code>{widget.kind === "form" ? formSnippet(widget) : widget.embed}</code>
                    <CopyButton text={widget.kind === "form" ? formSnippet(widget) : widget.embed} label="Copy the code" />
                </div>
                <div className="csheet__actions">
                    <button type="button" className="pill-button" onClick={onClose}>Done</button>
                </div>
            </div>
        </Modal>
    );
}

/* ── reach ─────────────────────────────────────────────────────────── */

function Reach({ pod }: { pod: Pod }) {
    const reach = useReach(pod.id);
    const change = useReachChange(pod.id);
    const [problem, setProblem] = useState<string | null>(null);
    const refused = (error: unknown) =>
        setProblem(isForbidden(error) ? "Only an editor of " + pod.name + " can change that." : "Couldn’t change it. Try again.");

    return (
        <section className="contacts__section" aria-labelledby="contacts-reach">
            <h2 id="contacts-reach">What contacts can use</h2>
            <p className="contacts__quiet">Beyond what is Public. A contact only ever sees their own rows, and a function is told which contact asked.</p>
            {reach.isPending ? (
                <p className="all__empty" role="status">Loading…</p>
            ) : !reach.data ? (
                <p role="alert">Couldn’t load it. <button type="button" className="linkish" onClick={() => void reach.refetch()}>Try again</button></p>
            ) : (
                <div className="creach">
                    <div>
                        <h3>Their own rows</h3>
                        {reach.data.tables.length === 0 ? <p className="contacts__quiet">No tables yet.</p> : reach.data.tables.map((table) => (
                            <label key={table.name} className="smanage__check">
                                <input
                                    type="checkbox"
                                    role="switch"
                                    checked={table.contactOwned}
                                    disabled={change.isPending || table.perPerson}
                                    onChange={(event) => change.mutate({ table: table.name, on: event.target.checked }, { onError: refused })}
                                />
                                <span>
                                    <span className="smanage__label">{table.name}</span>
                                    <small>{table.perPerson ? "Each member sees their own rows, so contacts can’t." : table.contactOwned ? "Rows with a contact_id are that contact’s." : "Not shown to contacts."}</small>
                                </span>
                            </label>
                        ))}
                    </div>
                    <div>
                        <h3>Functions they can ask for</h3>
                        {reach.data.functions.length === 0 ? <p className="contacts__quiet">No functions yet.</p> : reach.data.functions.map((fn) => (
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
                                    <small>{fn.description || (fn.contactsInvoke ? "Contacts can ask for it." : "Not offered to contacts.")}</small>
                                </span>
                            </label>
                        ))}
                    </div>
                </div>
            )}
            {problem && <p role="alert">{problem}</p>}
        </section>
    );
}
