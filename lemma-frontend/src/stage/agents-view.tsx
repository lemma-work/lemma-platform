"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { chosenModel, optionsFor, source } from "@/data";
import {
    REASONING_EFFORTS, agentProblems, draftOfAgent, hasAgentChanges, runtimeLine,
    type AgentDetail, type AgentDraft,
} from "@/data/agents";
import { CloseIcon, WarningIcon } from "@/ui/icons";

/** An agent's page, the parts worth sharing: what it runs on, the two fields
 *  worth editing by hand, and removing it. Talking to an agent is still the
 *  main way it changes; the editor is for the times you want to read the
 *  instruction rather than ask about it. */

/** "Runs on", by the profile's name rather than its id, and marked when the
 *  profile is retired. Under the model picker's key, which lists retired
 *  profiles too, so it is usually already loaded. */
export function useRunsOn(orgId: string | undefined, detail: AgentDetail): string {
    const runtimes = useQuery({
        queryKey: ["runtimes", orgId],
        queryFn: () => source.listRuntimes(orgId ?? ""),
        enabled: Boolean(orgId && detail.runtime),
        staleTime: 5 * 60_000,
    });
    return runtimeLine(detail, runtimes.data);
}

/* ── editing ───────────────────────────────────────────────────────── */

export function Editor({ podId, orgId, detail, onDone }: {
    podId: string;
    /** Where the agent's model is listed, to know whether it takes an effort. */
    orgId?: string;
    detail: AgentDetail;
    onDone: () => void;
}) {
    const cache = useQueryClient();
    /* Same key as the model picker's, so this is usually already loaded. */
    const runtimes = useQuery({
        queryKey: ["runtimes", orgId],
        queryFn: () => source.listRuntimes(orgId ?? ""),
        enabled: Boolean(orgId && detail.runtime),
        staleTime: 5 * 60_000,
    });
    const pinned = detail.runtime;
    const runtime = runtimes.data?.find((one) => one.id === pinned?.profile);
    const model = runtime?.models.find((one) => one.name === (pinned?.model || runtime.defaultModel));
    /* A coding agent takes none of the generation settings — it runs its own
       loop, with its own limits — so it gets the options it publishes for the
       pinned model instead, read off the profile on its own because the list
       carries no harness. Same key as the teammate's own picker. */
    const agent = runtime?.kind === "agent" ? runtime : undefined;
    const agentDetail = useQuery({
        queryKey: ["runtime", orgId, agent?.id],
        queryFn: () => source.getRuntime(orgId ?? "", agent?.id ?? ""),
        enabled: Boolean(orgId && agent),
        staleTime: 60_000,
    });
    const sets = agentDetail.data?.options;
    const agentOptions = agent && sets && pinned
        ? optionsFor(sets, chosenModel(agent, { runtimeId: agent.id, model: pinned.model }))
        : [];
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

            {/* A coding agent's own options, in place of the generation
                settings it would ignore. Each starts at "Agent's default",
                which sends nothing and leaves it as the runtime has it. */}
            {/* Said rather than left blank: an empty section reads as "this
                agent has no options", which is a different thing. */}
            {pinned && agent && agentDetail.isError && (
                <p className="empty-row" role="alert">
                    Couldn’t read {agent.name}’s options from its computer, so its effort stays as that computer has it.
                </p>
            )}
            {pinned && agent && agentOptions.length > 0 && (
                <div className="agents-generation">
                    {agentOptions.map((option) => (
                        <div className="record-form__field" key={option.key}>
                            <label htmlFor={"agent-option-" + option.key}>{option.label}</label>
                            {option.description && <small>{option.description}</small>}
                            <select
                                id={"agent-option-" + option.key}
                                value={draft.selections[option.key] ?? ""}
                                disabled={save.isPending}
                                onChange={(event) => setDraft({
                                    ...draft,
                                    selections: { ...draft.selections, [option.key]: event.target.value },
                                })}
                            >
                                <option value="">Agent’s default</option>
                                {option.choices.map((one) => (
                                    <option key={one.value} value={one.value}>{one.label}</option>
                                ))}
                            </select>
                        </div>
                    ))}
                </div>
            )}

            {/* Only on a pinned model: the settings belong to the runtime, and
                an agent on the pod's default has none of its own to edit. The
                effort only where the model says it reasons -- elsewhere it is
                never sent, and a control that does nothing is worse than none.
                Never on a coding agent, which reads none of them -- nor while
                the list that says which it is is still arriving. */}
            {pinned && !agent && !runtimes.isLoading && (
                <div className="agents-generation">
                    {(model?.reasons || draft.reasoningEffort) && (
                        <div className="record-form__field">
                            <label htmlFor="agent-effort">Reasoning effort</label>
                            <select
                                id="agent-effort"
                                value={draft.reasoningEffort}
                                disabled={save.isPending}
                                onChange={(event) => setDraft({ ...draft, reasoningEffort: event.target.value })}
                            >
                                <option value="">Model default</option>
                                {REASONING_EFFORTS.map((one) => (
                                    <option key={one} value={one}>{one[0].toUpperCase() + one.slice(1)}</option>
                                ))}
                            </select>
                        </div>
                    )}
                    <div className="record-form__field">
                        <label htmlFor="agent-max-output">Max output tokens</label>
                        <input
                            id="agent-max-output"
                            inputMode="numeric"
                            placeholder="Model limit"
                            value={draft.maxOutputTokens}
                            disabled={save.isPending}
                            onChange={(event) => setDraft({ ...draft, maxOutputTokens: event.target.value })}
                        />
                        {found.maxOutputTokens && <em role="alert">{found.maxOutputTokens}</em>}
                    </div>
                </div>
            )}

            {/* Toolsets, visibility, the runtime and the schemas are all on
                `UpdateAgentRequest` and none of them are here. Each is a
                decision with a consequence outside this page — a toolset
                grants tools and moves the memory folder grant with it, a
                visibility change can hide the agent from the person editing
                it — and a picker that writes them without saying so is worse
                than not offering them. */}
            <p className="agents-dim">
                Toolsets, visibility and the model are set by asking {detail.label} for them, or in settings.
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
export function ConfirmRemove({ detail, lost, busy, problem, onCancel, onConfirm }: {
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
