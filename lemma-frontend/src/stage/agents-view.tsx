import { AgentChannels } from "@/shell/agent-channels";
"use client";

import { LoadingRows } from "@/ui/loading";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import {
    AGENT_EDIT, AGENT_REMOVE, agentProblems, draftOfAgent, hasAgentChanges,
    may, runtimeLine, surfacesLost, whyNot,
    type AgentDetail, type AgentDraft, type AgentRow,
} from "@/data/agents";
import { displayAgentName } from "@/data/agent-names";
import { isForbidden } from "@/session/auth-state";
import { useSurfaces } from "@/shell/surfaces";
import { Mark } from "@/shell/mark";
import { BackIcon, ChatIcon, ChevronRightIcon, CloseIcon, WarningIcon } from "@/ui/icons";

/** What a teammate is actually made of.
 *
 *  A pod is one thing you talk to; behind it is the agent answering and
 *  whatever it delegates to. That list was readable in two places — six lines
 *  on the profile, and the search index — and changeable in none: the only way
 *  to alter an agent was to open a conversation with it and ask.
 *
 *  So: every agent, what each is for, what each may do, and — where the API
 *  will accept it — the two fields worth editing by hand. Talking to one is
 *  still here and still the main way an agent changes; this is for the times
 *  you want to read the instruction rather than ask about it.
 *
 *  Flat rows on a divider, like the library and the connector catalogue. A
 *  pod has a handful of agents and eight bordered boxes make the eye cross a
 *  border to get from one name to the next.
 */
export function AgentsView({ podId, teammate, embedded, open: opened, onOpen: setOpened, onDiscussAgent }: {
    /** Rendered inside the teammate's profile rather than as a view of its
     *  own. The profile's section already carries a heading, and a second one
     *  under it announced the same thing twice. */
    embedded?: boolean;
    podId: string;
    /** Whose agents these are, so the page can say whose. */
    teammate: string;
    /** Which one is showing, held by the shell rather than here: search can
     *  land straight on an agent, and a selection kept in this component
     *  would be thrown away every time the tab went behind a conversation. */
    open: string | null;
    onOpen: (name: string | null) => void;
    /** Open an agent's own conversation — still where an agent gets changed
     *  by talking to it, and the only route that works for all of them. */
    onDiscussAgent?: (name: string) => void;
}) {

    const agents = useQuery({
        queryKey: ["agents", podId],
        queryFn: () => source.listAgents(podId),
        staleTime: 60_000,
    });

    /* The pod's own assistant is left out, and that is not tidying: it *is*
       the teammate. This list sits on that teammate's profile, so putting it
       in means the page introduces itself twice, once as the subject and once
       as a row under its own staff. What belongs here is what it hands work
       to. */
    const rows = (agents.data ?? []).filter((row) => !row.front);

    if (opened) {
        return (
            <AgentDetailPane
                podId={podId}
                name={opened}
                teammate={teammate}
                onBack={() => setOpened(null)}
                onDiscussAgent={onDiscussAgent}
                onGone={() => setOpened(null)}
            />
        );
    }

    return (
        <section className={"agents-view" + (embedded ? " agents-view--embedded" : "")} aria-label="Agents">
            <header className="agents-head">
                {!embedded && (
                    <div>
                        <h1>Agents</h1>
                        <p>Who answers as {teammate}, and what it hands work to.</p>
                    </div>
                )}
            </header>

            {agents.isPending && <LoadingRows label="Loading teammates" />}
            {agents.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(agents.error)
                        ? "You may not list the agents for this teammate."
                        : "Couldn’t load agents."}{" "}
                    <button className="linkish" onClick={() => void agents.refetch()}>Try again</button>
                </p>
            )}

            {agents.isSuccess && rows.length === 0 && (
                <p className="empty-row">
                    {teammate} does its own work, and hands nothing on.
                </p>
            )}

            {rows.length > 0 && (
                <div className="agents-list">
                    {/* Keyed by position as well as name: a payload can carry
                        more than one nameless row, and two of them would
                        otherwise share a key. */}
                    {rows.map((row, at) => (
                        <Row key={row.name || "unnamed-" + at} podId={podId} row={row} onOpen={() => setOpened(row.name)} />
                    ))}
                </div>
            )}

            {agents.isSuccess && rows.length > 0 && (
                <p className="agents-note">
                    An agent is changed by talking to it, or — for the ones you may edit — here.
                </p>
            )}
        </section>
    );
}

function Row({ podId, row, onOpen }: { podId: string; row: AgentRow; onOpen: () => void }) {
    return (
        <div className="agent-row" data-front={row.front || undefined} data-broken={row.broken || undefined}>
            <button className="agent-row__open" onClick={onOpen} disabled={row.broken}>
                {/* The same seed as the colleague cards on the profile, so an
                    agent wears one face wherever it is drawn. */}
                <Mark seed={podId + ":" + row.name} name={row.label} icon={row.iconUrl} size={30} />
                <span className="agent-row__body">
                    <span className="agent-row__title">
                        <strong>{row.label}</strong>
                        {row.front && <em className="agent-tag agent-tag--front">answers here</em>}
                        {row.takesInput && <em className="agent-tag">takes arguments</em>}
                        {row.visibility === "RESTRICTED" && <em className="agent-tag">restricted</em>}
                    </span>
                    <small>{row.blurb || "No description written."}</small>
                </span>
                {row.can.length > 0 && (
                    <span className="agent-row__can">
                        {row.can.slice(0, 3).join(" · ")}
                        {row.can.length > 3 && " +" + (row.can.length - 3)}
                    </span>
                )}
                {!row.broken && <ChevronRightIcon size={16} />}
            </button>
        </div>
    );
}

/* ── one agent ─────────────────────────────────────────────────────── */

function AgentDetailPane({ podId, name, teammate, onBack, onDiscussAgent, onGone }: {
    podId: string;
    name: string;
    teammate: string;
    onBack: () => void;
    onDiscussAgent?: (name: string) => void;
    onGone: () => void;
}) {
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [removing, setRemoving] = useState(false);

    const agent = useQuery({
        queryKey: ["agent", podId, name],
        queryFn: () => source.getAgent(podId, name),
        staleTime: 30_000,
    });
    /* Already on screen elsewhere in this pod, so it comes from the same cache
       rather than being fetched again for the sake of one confirmation. */
    const surfaces = useSurfaces(podId);

    const remove = useMutation({
        mutationFn: () => source.deleteAgent(podId, name),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["agents", podId] });
            void cache.invalidateQueries({ queryKey: ["colleagues", podId] });
            void cache.invalidateQueries({ queryKey: ["surfaces", 2, podId] });
            onGone();
        },
    });

    const detail = agent.data;
    const lost = detail ? surfacesLost(surfaces.data ?? [], detail.label) : [];
    /* What to call it before the detail has landed. The row name is the only
       thing known at that point, and printing it raw says "Reading
       pod_default…" — the one name this product deliberately never shows a
       person. The same rule the rest of the app goes through. */
    const calling = displayAgentName(name);

    return (
        <section className="agents-view agents-view--one" aria-label={"Agent " + calling}>
            <button className="agents-back linkish" onClick={onBack}>
                <BackIcon size={15} /> All agents
            </button>

            {agent.isPending && <LoadingRows label={"Loading " + calling} />}
            {agent.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(agent.error)
                        ? "You may not read " + calling + "."
                        : "Couldn’t load " + calling + "."}{" "}
                    <button className="linkish" onClick={() => void agent.refetch()}>Try again</button>
                </p>
            )}

            {detail && (
                <>
                    <header className="agents-head agents-head--one">
                        <Mark seed={podId + ":" + detail.name} name={detail.label} icon={detail.iconUrl} size={42} />
                        <div>
                            <h1>{detail.label}</h1>
                            {/* The row name, because that is what every call
                                is keyed by and what the instruction above
                                names when it delegates. The label is not. */}
                            <p><code>{detail.name}</code>{detail.front && " · answers as " + teammate}</p>
                        </div>
                        <div className="agents-acts">
                            {onDiscussAgent && !detail.takesInput && (
                                <button className="btn" onClick={() => onDiscussAgent(detail.name)}>
                                    <ChatIcon size={15} /> Talk to it
                                </button>
                            )}
                            {may(detail, AGENT_EDIT) && !editing && (
                                <button className="btn" onClick={() => setEditing(true)}>Edit</button>
                            )}
                            {may(detail, AGENT_REMOVE) && !editing && (
                                <button className="btn" onClick={() => setRemoving(true)}>Delete</button>
                            )}
                        </div>
                    </header>

                    {/* Said rather than shown as a disabled button. A control
                        that is greyed out with nothing to say for itself is a
                        question the page refuses to answer. */}
                    {!may(detail, AGENT_EDIT) && (
                        <p className="agents-refusal">
                            <WarningIcon size={14} /> {whyNot(detail, AGENT_EDIT)}
                        </p>
                    )}

                    {removing && (
                        <ConfirmRemove
                            detail={detail}
                            lost={lost}
                            busy={remove.isPending}
                            problem={remove.error instanceof Error ? remove.error.message : ""}
                            onCancel={() => { setRemoving(false); remove.reset(); }}
                            onConfirm={() => remove.mutate()}
                        />
                    )}

                    <AgentChannels podId={podId} name={detail.name} label={detail.label} />

                    {editing ? (
                        /* Keyed by the agent, so the draft is seeded once per
                           agent and a refetch cannot overwrite it. Keying on
                           the fetched object's identity instead throws away
                           whatever has been typed whenever somebody tabs away
                           for half a minute and back — the refetch returns a
                           new object every time. */
                        <Editor
                            key={detail.name}
                            podId={podId}
                            detail={detail}
                            onDone={() => setEditing(false)}
                        />
                    ) : (
                        <Reading detail={detail} />
                    )}
                </>
            )}
        </section>
    );
}

/** What the agent is, read rather than edited. */
function Reading({ detail }: { detail: AgentDetail }) {
    const runtime = runtimeLine(detail);
    return (
        <>
            {detail.description && <p className="agents-blurb">{detail.description}</p>}

            <Block title="Instruction" meta={detail.instruction.length.toLocaleString() + " characters"}>
                {detail.instruction
                    ? <pre className="agents-instruction">{detail.instruction}</pre>
                    : <p className="empty-row">Couldn’t load this agent’s instructions.</p>}
            </Block>

            <Block title="Can" meta={detail.can.length ? String(detail.can.length) : undefined}>
                {detail.can.length
                    ? <div className="agent-cans">{detail.can.map((one) => <span className="agent-can" key={one}>{one}</span>)}</div>
                    : <p className="empty-row">No toolsets. It can read and write its reply, and nothing else.</p>}
            </Block>

            <Block title="Runs on">
                {runtime
                    ? <p className="agents-line"><code>{runtime}</code></p>
                    : <p className="empty-row">Nothing pinned — it runs on whatever this teammate runs on.</p>}
            </Block>

            {/* Only where there is one. An agent without an input schema is
                talked to, and a section saying "no input schema" on every
                conversational agent in the pod is five lines of nothing. */}
            {(detail.input || detail.output) && (
                <Block title="Called with">
                    {detail.input && <Schema label="Input" note={detail.input} />}
                    {detail.output && <Schema label="Output" note={detail.output} />}
                </Block>
            )}

            {detail.grants.length > 0 && (
                <Block title="Reaches" meta={String(detail.grants.length)}>
                    <ul className="agent-grants">
                        {detail.grants.map((grant) => (
                            <li key={grant.resource + grant.name}>
                                <code>{grant.name}</code>
                                <small>{grant.resource.toLowerCase().replace(/_/g, " ")}</small>
                                <span>{grant.permissions.join(", ")}</span>
                            </li>
                        ))}
                    </ul>
                </Block>
            )}

            <Block title="You may">
                {detail.actions.length
                    ? <div className="agent-cans">
                        {detail.actions.map((action) => (
                            <span
                                className="agent-can"
                                key={action}
                                data-off={!may(detail, action) || undefined}
                                title={whyNot(detail, action) || undefined}
                            >{action}</span>
                        ))}
                    </div>
                    : <p className="empty-row">Nothing beyond seeing it listed.</p>}
            </Block>
        </>
    );
}

function Schema({ label, note }: { label: string; note: { fields: string[]; required: string[]; opaque: boolean } }) {
    return (
        <p className="agents-line">
            <b>{label}</b>{" "}
            {note.opaque
                ? <span className="agents-dim">declared, but not a flat object this page can name.</span>
                : note.fields.map((field) => (
                    <code key={field} data-required={note.required.includes(field) || undefined}>{field}</code>
                ))}
        </p>
    );
}

function Block({ title, meta, children }: { title: string; meta?: string; children: React.ReactNode }) {
    return (
        <div className="agents-block">
            <h2>{title}{meta && <b>{meta}</b>}</h2>
            {children}
        </div>
    );
}

/* ── editing ───────────────────────────────────────────────────────── */

function Editor({ podId, detail, onDone }: { podId: string; detail: AgentDetail; onDone: () => void }) {
    const cache = useQueryClient();
    /* What the server holds, re-read on every render: the PATCH diff is
       against the *current* saved state, so a field somebody never touched is
       never resent — which is the whole point of sending only what changed. */
    const before = useMemo(() => draftOfAgent(detail), [detail]);
    /* Seeded once. This component is keyed by the agent, so opening a
       different one mounts a fresh editor rather than reusing this draft. */
    const [draft, setDraft] = useState<AgentDraft>(before);

    const found = agentProblems(draft);
    const dirty = hasAgentChanges(before, draft);

    const save = useMutation({
        mutationFn: () => source.updateAgent(podId, detail.name, before, draft),
        onSuccess: (saved) => {
            cache.setQueryData(["agent", podId, detail.name], saved);
            void cache.invalidateQueries({ queryKey: ["agents", podId] });
            void cache.invalidateQueries({ queryKey: ["colleagues", podId] });
            /* The profile's About is the default agent's instruction. */
            void cache.invalidateQueries({ queryKey: ["profile", podId] });
            onDone();
        },
    });

    return (
        <form
            className="agents-form record-form"
            onSubmit={(event) => { event.preventDefault(); if (dirty && !Object.keys(found).length) save.mutate(); }}
        >
            <div className="record-form__field">
                <label htmlFor="agent-description">Description</label>
                <small>One line, shown wherever this agent is listed. Clearing it is allowed.</small>
                <input
                    id="agent-description"
                    value={draft.description}
                    disabled={save.isPending}
                    onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                />
            </div>

            <div className="record-form__field">
                <label htmlFor="agent-instruction">Instruction<i aria-hidden="true"> *</i></label>
                <small>Instructions it follows each time it runs.</small>
                <textarea
                    id="agent-instruction"
                    className="agents-editor"
                    rows={18}
                    value={draft.instruction}
                    disabled={save.isPending}
                    onChange={(event) => setDraft({ ...draft, instruction: event.target.value })}
                />
                {found.instruction && <em role="alert">{found.instruction}</em>}
            </div>

            {/* Toolsets, visibility, the runtime and the schemas are all on
                `UpdateAgentRequest` and none of them are here. Each is a
                decision with a consequence outside this page — a toolset
                grants tools and moves the memory folder grant with it, a
                visibility change can hide the agent from the person editing
                it — and a picker that writes them without saying so is worse
                than not offering them. */}
            <p className="agents-dim">
                Toolsets, visibility and the model are set by asking {detail.label} for them, or in teammate settings.
            </p>

            {save.isError && (
                <p className="record-form__problem" role="alert">
                    {save.error instanceof Error ? save.error.message : "That was not saved."}
                </p>
            )}

            <div className="record-form__actions">
                <button className="btn btn--primary" type="submit" disabled={!dirty || save.isPending || Object.keys(found).length > 0}>
                    {save.isPending ? "Saving…" : "Save"}
                </button>
                <button className="btn" type="button" disabled={save.isPending} onClick={onDone}>Cancel</button>
                {dirty && !save.isPending && (
                    <button className="record-form__revert" type="button" onClick={() => setDraft(before)}>Put it back</button>
                )}
            </div>
        </form>
    );
}

/** Named, and it says what breaks rather than asking "are you sure?". */
function ConfirmRemove({ detail, lost, busy, problem, onCancel, onConfirm }: {
    detail: AgentDetail;
    lost: { platform: string; handle: string }[];
    busy: boolean;
    problem: string;
    onCancel: () => void;
    onConfirm: () => void;
}) {
    return (
        <div className="agents-confirm" role="alertdialog" aria-label={"Delete " + detail.label}>
            <p>
                Delete <strong>{detail.label}</strong>? Its instruction goes with it, anything holding
                a token for it stops working immediately, and {detail.label} can no longer be handed work.
            </p>
            {lost.length > 0 && (
                <p className="agents-confirm__breaks">
                    <WarningIcon size={14} />
                    {lost.length === 1 ? " This address stops answering: " : " These addresses stop answering: "}
                    {lost.map((one) => one.handle + " (" + one.platform.toLowerCase() + ")").join(", ")}.
                </p>
            )}
            {problem && <p className="record-form__problem" role="alert">{problem}</p>}
            <div className="record-form__actions">
                <button className="btn btn--danger" disabled={busy} onClick={onConfirm}>
                    {busy ? "Deleting…" : "Delete " + detail.label}
                </button>
                <button className="btn" disabled={busy} onClick={onCancel}>Keep it</button>
            </div>
            <button className="agents-confirm__close" aria-label="Cancel" onClick={onCancel} disabled={busy}>
                <CloseIcon size={14} />
            </button>
        </div>
    );
}
