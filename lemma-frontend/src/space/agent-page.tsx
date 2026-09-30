"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod, type Surface } from "@/data";
import { AskBox } from "@/chat/ask-box";
import { lemma } from "@/session/client";
import {
    AGENT_EDIT, AGENT_REMOVE, AGENT_RUN, may, runtimeLine, surfacesLost, whyNot,
    type AgentDetail,
} from "@/data/agents";
import { displayAgentName } from "@/data/agent-names";
import { surfacesForAgent, surfaceStatus } from "@/data/surface-settings";
import { capabilityList, grantedToolsets } from "@/stage/colleagues";
import { ConfirmRemove, Editor } from "@/stage/agents-view";
import { useSchedules } from "@/schedule/queries";
import { wakeFor, wakeRequest } from "@/docpages/comments/wake";
import { readWorkflows, sayWhen } from "@/workflow/runs";
import { isForbidden } from "@/session/auth-state";
import { useSurfaces } from "@/shell/surfaces";
import { ChannelIcon, channelName } from "@/shell/channels";
import { Modal } from "@/shell/modal";
import { SurfaceManage } from "@/shell/surface-manage";
import { AgentMark } from "./agent-mark";
import { ChatIcon, ChevronLeftIcon, ChevronRightIcon, ClockIcon, EditIcon, LockIcon, WorkflowIcon } from "@/ui/icons";

/** One agent, as a page of its own.
 *
 *  Most people talk to the teammate and never come here — its own agent is the
 *  teammate, and opens as About instead. This page is for the others: the
 *  specialist somebody set up, the one a workflow hands work to. So it answers the three questions somebody arrives with, in
 *  that order: who is this and what is it for; let me ask it something (or
 *  pick up where I left off); and what is it actually allowed to do, and who
 *  depends on it. The last is a rail beside the first two rather than a
 *  wall of headings under them. */
export function AgentPage({ pod, name, live, onBack, onOpenConversation, onAsk, onOpenSchedules, onOpenWorkflows }: {
    pod: Pod;
    name: string;
    live: boolean;
    onBack: () => void;
    onOpenConversation: (id: string) => void;
    /** Start a conversation in the Chat tab, with this bot when it is named. */
    onAsk: (text: string, createWith?: Record<string, unknown>) => void;
    onOpenSchedules: () => void;
    onOpenWorkflows: () => void;
}) {
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [removing, setRemoving] = useState(false);

    const agent = useQuery({
        queryKey: ["agent", pod.id, name],
        queryFn: () => source.getAgent(pod.id, name),
        staleTime: 30_000,
    });
    const surfaces = useSurfaces(pod.id);
    const remove = useMutation({
        mutationFn: () => source.deleteAgent(pod.id, name),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["agents", pod.id] });
            void cache.invalidateQueries({ queryKey: ["colleagues", pod.id] });
            void cache.invalidateQueries({ queryKey: ["surfaces", 2, pod.id] });
            onBack();
        },
    });

    const detail = agent.data;
    const calling = detail?.label ?? displayAgentName(name);

    return (
        <div className="agentpage">
            <div className="agentpage__inner">
                <nav className="runpage__crumb agentpage__crumb">
                    <button onClick={onBack}><ChevronLeftIcon size={15} /> {pod.name}</button>
                </nav>

                {agent.isPending && <p className="agentpage__note" role="status">Opening {calling}…</p>}
                {agent.isError && (
                    <p className="agentpage__note" role="alert">
                        {isForbidden(agent.error) ? "You may not open " + calling + "." : "Couldn’t load " + calling + "."}{" "}
                        <button className="linkish" onClick={() => void agent.refetch()}>Try again</button>
                    </p>
                )}

                {detail && (
                    <>
                        <header className="agentpage__head">
                            <AgentMark pod={pod} agent={detail} size={64} />
                            <div className="agentpage__who">
                                <h1>{detail.label}</h1>
                                <p>{detail.description || detail.blurb || "No description written."}</p>
                                <div className="agentpage__tags">
                                    {detail.visibility === "RESTRICTED" && <span className="agentpage__tag"><LockIcon size={12} /> Restricted</span>}
                                    {detail.takesInput && <span className="agentpage__tag">Called by workflows</span>}
                                </div>
                            </div>
                            <div className="agentpage__acts">
                                {may(detail, AGENT_EDIT) && !editing && (
                                    <button className="agentpage__btn" onClick={() => setEditing(true)}><EditIcon size={15} /> Edit</button>
                                )}
                                {may(detail, AGENT_REMOVE) && !editing && !detail.front && (
                                    <button className="agentpage__btn agentpage__btn--quiet" onClick={() => setRemoving(true)}>Delete</button>
                                )}
                            </div>
                        </header>

                        {removing && (
                            <ConfirmRemove
                                detail={detail}
                                lost={surfacesLost(surfaces.data ?? [], detail.label)}
                                busy={remove.isPending}
                                problem={remove.error instanceof Error ? remove.error.message : ""}
                                onCancel={() => { setRemoving(false); remove.reset(); }}
                                onConfirm={() => remove.mutate()}
                            />
                        )}

                        <div className="agentpage__grid">
                            <div className="agentpage__main">
                                {editing ? (
                                    <section className="agentpage__card">
                                        <h2>Edit {detail.label}</h2>
                                        <Editor key={detail.name} podId={pod.id} detail={detail} onDone={() => setEditing(false)} />
                                    </section>
                                ) : (
                                    <>
                                        <Ask detail={detail} onAsk={onAsk} />
                                        <Conversations pod={pod} detail={detail} live={live} onOpen={onOpenConversation} />
                                    </>
                                )}
                            </div>
                            <aside className="agentpage__rail" aria-label={"About " + detail.label}>
                                <Instructions detail={detail} />
                                <Abilities detail={detail} />
                                <Channels podId={pod.id} detail={detail} surfaces={surfaces.data ?? []} loading={surfaces.isPending} />
                                <Facts detail={detail} />
                                <CommentReplies pod={pod} detail={detail} />
                                <UsedBy pod={pod} detail={detail} onOpenSchedules={onOpenSchedules} onOpenWorkflows={onOpenWorkflows} />
                                <Access pod={pod} detail={detail} />
                            </aside>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

/* ── talking to it ─────────────────────────────────────────────────── */

/** The box. Sending starts a conversation with this bot — made with its
 *  `agent_name`, so it is this bot answering — in the Chat tab. */
function Ask({ detail, onAsk }: { detail: AgentDetail; onAsk: (text: string, createWith?: Record<string, unknown>) => void }) {
    if (detail.takesInput) {
        return (
            <p className="agentpage__card agentpage__called">
                {detail.label} is called with arguments — by a workflow or another agent — rather than talked to.
            </p>
        );
    }
    if (!may(detail, AGENT_RUN) && detail.actions.length > 0) {
        return <p className="agentpage__card agentpage__called">You can read about {detail.label}, but not talk to it.</p>;
    }
    return (
        <div className="agentpage__ask">
            <AskBox placeholder={"Message " + detail.label + "…"}
                onAsk={(text) => onAsk(text, detail.front ? undefined : { agent_name: detail.name })} />
        </div>
    );
}

interface ChatRow { id: string; title: string; at: string | null }

function Conversations({ pod, detail, live, onOpen }: { pod: Pod; detail: AgentDetail; live: boolean; onOpen: (id: string) => void }) {
    const [all, setAll] = useState(false);
    const chats = useQuery({
        queryKey: ["bot-conversations", pod.id, detail.name],
        enabled: live && !detail.takesInput,
        staleTime: 30_000,
        queryFn: async (): Promise<ChatRow[]> => {
            const client = lemma(pod.id).conversations;
            const listed = detail.front
                ? await client.listDefault({ pod_id: pod.id, limit: 30 })
                : await client.listByAgent(detail.name, { pod_id: pod.id, limit: 30 });
            return ((listed as { items?: unknown[] }).items ?? []).map((raw) => {
                const row = raw as { id: string; title?: string | null; last_activity_at?: string | null; updated_at?: string | null };
                return { id: row.id, title: (row.title ?? "").trim() || "Untitled", at: row.last_activity_at ?? row.updated_at ?? null };
            });
        },
    });
    if (detail.takesInput) return null;
    const rows = chats.data ?? [];
    const shown = all ? rows : rows.slice(0, 6);
    return (
        <section className="agentpage__section">
            <h2>Conversations</h2>
            {!live && <p className="agentpage__quiet">Conversations show here once you are signed in.</p>}
            {live && chats.isPending && <p className="agentpage__quiet">Loading…</p>}
            {live && chats.isError && <p className="agentpage__quiet">Couldn’t load conversations.</p>}
            {live && chats.isSuccess && rows.length === 0 && (
                <p className="agentpage__quiet">You haven’t talked to {detail.label} yet.</p>
            )}
            {shown.length > 0 && (
                <ul className="agentpage__chats">
                    {shown.map((row) => (
                        <li key={row.id}>
                            <button onClick={() => onOpen(row.id)}>
                                <ChatIcon size={16} />
                                <span>{row.title}</span>
                                <small>{sayWhen(row.at)}</small>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
            {rows.length > 6 && (
                <button className="agentpage__more" onClick={() => setAll((v) => !v)}>
                    {all ? "Show fewer" : "Show all " + rows.length}
                </button>
            )}
        </section>
    );
}

/* ── the rail: what it is made of ──────────────────────────────────── */

function Rail({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
    return (
        <section className="agentpage__block">
            <header><h2>{title}</h2>{action}</header>
            {children}
        </section>
    );
}

function Instructions({ detail }: { detail: AgentDetail }) {
    const [open, setOpen] = useState(false);
    if (detail.front && !detail.instruction) {
        return (
            <Rail title="Instructions">
                <p className="agentpage__quiet">Follows the space’s own memory and skills.</p>
            </Rail>
        );
    }
    const long = detail.instruction.length > 420 || detail.instruction.split("\n").length > 8;
    return (
        <Rail title="Instructions" action={long ? (
            <button className="agentpage__link" onClick={() => setOpen((v) => !v)}>{open ? "Show less" : "Show all"}</button>
        ) : undefined}>
            {detail.instruction
                ? <p className="agentpage__instruction" data-open={open || !long || undefined}>{detail.instruction}</p>
                : <p className="agentpage__quiet">None written.</p>}
        </Rail>
    );
}

function Abilities({ detail }: { detail: AgentDetail }) {
    const list = capabilityList(grantedToolsets(detail.toolsets, detail.front));
    return (
        <Rail title="Can">
            {list.length === 0 ? <p className="agentpage__quiet">Only read and reply — no tools.</p> : (
                <ul className="agentpage__abilities">
                    {list.map((one) => (
                        <li key={one.code}><b>{one.word}</b><span>{one.says}</span></li>
                    ))}
                </ul>
            )}
            {detail.grants.length > 0 && (
                <ul className="agentpage__grants">
                    {detail.grants.map((grant) => (
                        <li key={grant.resource + grant.name}>
                            <span>{grant.name}</span>
                            <small>{grant.resource.toLowerCase().replace(/_/g, " ")} · {sayPermissions(grant.permissions)}</small>
                        </li>
                    ))}
                </ul>
            )}
        </Rail>
    );
}

/** `document.read`, `document.write` → "can read and write". */
function sayPermissions(codes: string[]): string {
    const verbs = [...new Set(codes.map((code) => code.split(".").pop()!.toLowerCase().replace(/_/g, " ")))];
    if (verbs.length === 0) return "no access";
    return "can " + (verbs.length === 1 ? verbs[0] : verbs.slice(0, -1).join(", ") + " and " + verbs[verbs.length - 1]);
}

function Channels({ podId, detail, surfaces, loading }: { podId: string; detail: AgentDetail; surfaces: Surface[]; loading: boolean }) {
    const cache = useQueryClient();
    const [selected, setSelected] = useState<Surface | null>(null);
    const pod = useQuery({ queryKey: ["surface-pod", podId], queryFn: () => source.getPod(podId), enabled: Boolean(selected) });
    const own = surfacesForAgent(surfaces, detail.front ? "pod_default" : detail.name);
    const saved = () => {
        for (const key of [["surfaces", 2, podId], ["surface-detail", podId], ["surface-setup", podId], ["surface-channels", podId], ["my-surfaces"]]) {
            void cache.invalidateQueries({ queryKey: key });
        }
        setSelected(null);
    };
    return (
        <Rail title="Reach it on">
            {loading ? <p className="agentpage__quiet">Loading…</p>
                : own.length === 0 ? <p className="agentpage__quiet">Only here, in the app.</p>
                : (
                    <ul className="agentpage__channels">
                        {own.map((surface) => (
                            <li key={surface.id}>
                                <ChannelIcon platform={surface.platform} size={18} />
                                <span className="agentpage__channel">
                                    <b>{channelName(surface.platform)}</b>
                                    {surface.handle && <small>{surface.handle}</small>}
                                </span>
                                <span className="agentpage__status" data-ok={(surface.active && (!surface.status || surface.status === "ACTIVE")) || undefined}>
                                    {surfaceStatus(surface.status, surface.active)}
                                </span>
                                <button className="agentpage__link" onClick={() => setSelected(surface)}>Manage</button>
                            </li>
                        ))}
                    </ul>
                )}
            {selected && (
                <Modal title={"Channels for " + detail.label} onClose={() => setSelected(null)}>
                    {pod.isPending && <p role="status">Loading channel settings…</p>}
                    {(pod.isError || (pod.isSuccess && !pod.data)) && <p role="alert">Could not load the space. <button className="btn" onClick={() => void pod.refetch()}>Retry</button></p>}
                    {pod.data && <SurfaceManage pod={pod.data} surface={selected} onBack={() => setSelected(null)} onSaved={saved} />}
                </Modal>
            )}
        </Rail>
    );
}

function Facts({ detail }: { detail: AgentDetail }) {
    const runtime = runtimeLine(detail);
    return (
        <Rail title="Runs on">
            <p className="agentpage__fact">{runtime || "The space’s default model"}</p>
            {(detail.input || detail.output) && (
                <dl className="agentpage__schema">
                    {detail.input && <><dt>Takes</dt><dd>{detail.input.opaque ? "structured input" : detail.input.fields.join(", ")}</dd></>}
                    {detail.output && <><dt>Returns</dt><dd>{detail.output.opaque ? "structured output" : detail.output.fields.join(", ")}</dd></>}
                </dl>
            )}
        </Rail>
    );
}

function UsedBy({ pod, detail, onOpenSchedules, onOpenWorkflows }: {
    pod: Pod;
    detail: AgentDetail;
    onOpenSchedules: () => void;
    onOpenWorkflows: () => void;
}) {
    const schedules = useSchedules(pod.id);
    const flows = useQuery({
        queryKey: ["workflows", pod.id, "names"],
        staleTime: 5 * 60_000,
        queryFn: async () => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOWS } = await import("@/data/fixtures");
                return readWorkflows({ items: SAMPLE_WORKFLOWS });
            }
            return readWorkflows(await lemma(pod.id).workflows.list({ limit: 100 }));
        },
    });
    const wire = detail.front ? "POD_DEFAULT" : detail.name;
    const jobs = (schedules.data ?? []).filter((job) => job.target.kind === "agent" && (job.target.name === wire || job.target.name === detail.name));
    const used = (flows.data ?? []).filter((flow) => flow.targets.includes("agent:" + detail.name) || flow.targets.includes("agent:" + wire));
    if (jobs.length === 0 && used.length === 0) {
        return (
            <Rail title="Used by">
                <p className="agentpage__quiet">No schedule or workflow hands it work.</p>
            </Rail>
        );
    }
    return (
        <Rail title="Used by">
            <ul className="agentpage__used">
                {jobs.map((job) => (
                    <li key={"s" + job.id}>
                        <button onClick={onOpenSchedules}>
                            <ClockIcon size={15} /><span>{job.title}</span><small>{job.trigger}</small><ChevronRightIcon size={14} />
                        </button>
                    </li>
                ))}
                {used.map((flow) => (
                    <li key={"w" + flow.id}>
                        <button onClick={onOpenWorkflows}>
                            <WorkflowIcon size={15} /><span>{flow.name}</span><small>Workflow</small><ChevronRightIcon size={14} />
                        </button>
                    </li>
                ))}
            </ul>
        </Rail>
    );
}

/** Whether a comment that @-mentions this bot wakes it. The switch is a
 *  schedule on the comments table (see `pages/comments/wake.ts`). */
function CommentReplies({ pod, detail }: { pod: Pod; detail: AgentDetail }) {
    const cache = useQueryClient();
    const schedules = useSchedules(pod.id);
    const key = detail.front ? "POD_DEFAULT" : detail.name;
    const job = wakeFor(schedules.data ?? [], key);
    const flip = useMutation({
        mutationFn: async () => {
            if (job) await lemma(pod.id).schedules.delete(job.id);
            else await lemma(pod.id).request("POST", "/pods/" + pod.id + "/schedules", { body: wakeRequest(key, detail.label) });
        },
        onSuccess: () => void cache.invalidateQueries({ queryKey: ["schedules", pod.id] }),
    });
    if (detail.takesInput) return null;
    return (
        <Rail title="Comments">
            <label className="agentpage__switch">
                <input type="checkbox" checked={Boolean(job)} disabled={flip.isPending || source.label !== "live" || schedules.isPending}
                    onChange={() => flip.mutate()} />
                <span>Answers comments that mention @{detail.label}</span>
            </label>
            <p className="agentpage__quiet">
                {job ? "Mention it on any page and it reads the page, does what you ask, and replies in the thread." : "Off: a mention in a comment is only a mention."}
            </p>
            {flip.isError && <p className="agentpage__quiet">{isForbidden(flip.error) ? "Only an editor of this space can change this." : "Couldn’t change that."}</p>}
        </Rail>
    );
}

/** Who may use it, and what you may do to it — as a sentence, never as the
 *  permission codes the API returns. */
function Access({ pod, detail }: { pod: Pod; detail: AgentDetail }) {
    const verbs: string[] = [];
    if (may(detail, AGENT_RUN)) verbs.push("use it");
    if (may(detail, AGENT_EDIT)) verbs.push("change it");
    if (may(detail, AGENT_REMOVE) && !detail.front) verbs.push("delete it");
    const you = verbs.length === 0
        ? "You can see it, but not use or change it."
        : "You can " + (verbs.length === 1 ? verbs[0] : verbs.slice(0, -1).join(", ") + " and " + verbs[verbs.length - 1]) + ".";
    const why = !may(detail, AGENT_EDIT) ? whyNot(detail, AGENT_EDIT) : "";
    return (
        <Rail title="Access">
            <p className="agentpage__fact">
                {detail.visibility === "RESTRICTED"
                    ? "Only the people it is shared with can use it."
                    : "Everyone in " + pod.name + " can use it."}
            </p>
            <p className="agentpage__quiet">{you}{why && " " + why}</p>
        </Rail>
    );
}
