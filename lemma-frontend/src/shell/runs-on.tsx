import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    source,
    agentDefaultLabel,
    agentLogo,
    chosenModel,
    describeChoice,
    offeredSelections,
    optionsFor,
    sameChoice,
    saidAbout,
    shortModel,
    type AgentOption,
    type Choice,
    type Runtime,
} from "@/data";
import { KeyIcon, LemmaMark, SparkleIcon, TerminalIcon, WarningIcon } from "@/ui/icons";
import { AddModelAction } from "@/thread/add-model-action";
import { openSettings } from "@/desktop/open-settings";
import { modelSetupState } from "./runs-on-state";

/** What this teammate thinks with.
 *
 *  The list it picks from belongs to the organization — one key is bought
 *  once, and a paired computer's agents follow the person who paired them —
 *  but the choice is the teammate's own, and it is the thing people change
 *  often. So the ledger is in settings and this is on the profile, which is
 *  the page that already says what this teammate is standing on.
 *
 *  Told nothing, a teammate inherits, and that is a real state rather than an
 *  empty one: the first row names what inheriting currently means, so nobody
 *  has to pick a model to find out what they already have. */

function Mark({ runtime }: { runtime: Runtime | undefined }) {
    const logo = runtime ? agentLogo(runtime.harness) : "";
    if (logo) return <img className="runson__logo" src={logo} alt="" aria-hidden="true" />;
    /* What Lemma provides carries Lemma's mark. The sparkle stays on the
       unpinned row, where the answer is whatever the organization uses and
       that is often somebody else's key. */
    if (runtime?.scope === "system") return <LemmaMark size={15} />;
    if (!runtime) return <SparkleIcon size={15} />;
    return runtime.kind === "agent" ? <TerminalIcon size={15} /> : <KeyIcon size={15} />;
}

/** Whether a model's label and its id are the same name wearing different
 *  punctuation. */
function sameName(label: string, model: string): boolean {
    const plain = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, "");
    return plain(label) === plain(shortModel(model));
}

/** Every model, flattened into the rows a person picks from. A runtime that
 *  offers nothing by name still gets one row — it runs *something*, and the
 *  backend resolves which at dispatch.
 *
 *  An unpinned coding agent gets that row as well, first, named for what it
 *  is: dispatch sends no model, so the agent runs whatever it is set to on
 *  its computer. Showing only its models made the first of them look chosen
 *  when nothing was. */
function rowsOf(runtimes: Runtime[]): { runtime: Runtime; choice: Choice; label: string }[] {
    return runtimes.flatMap((runtime) => {
        const own = { runtime, choice: { runtimeId: runtime.id, model: "" }, label: "" };
        if (runtime.models.length === 0) return [{ ...own, label: "its usual model" }];
        const models = runtime.models.map((model) => ({
            runtime,
            choice: { runtimeId: runtime.id, model: model.name },
            label: model.label,
        }));
        return runtime.kind === "agent" && !runtime.defaultModel
            ? [{ ...own, label: agentDefaultLabel() }, ...models]
            : models;
    });
}

/** The options a coding agent offers beside the model a choice runs, effort
 *  first — empty for a provider key, for a choice that pins nothing, and
 *  until the runtime's own read arrives.
 *
 *  Read off the profile on its own rather than the list, which carries no
 *  harness: the options are whatever that computer publishes now, and they
 *  move with the model. */
function useAgentOptions(orgId: string, runtime: Runtime | undefined, choice: Choice | null) {
    const agent = runtime?.kind === "agent" && choice?.runtimeId === runtime.id ? runtime : undefined;
    const detail = useQuery({
        queryKey: ["runtime", orgId, agent?.id],
        queryFn: () => source.getRuntime(orgId, agent?.id ?? ""),
        enabled: Boolean(agent),
        staleTime: 60_000,
    });
    const sets = detail.data?.options;
    const options = useMemo(
        () => (agent && sets ? optionsFor(sets, chosenModel(agent, choice)) : []),
        [agent, sets, choice],
    );
    return { options, sets, unreadable: detail.isError };
}

/** What a newly picked row keeps of the choice before it. Only the same
 *  agent's selections, and only those the new model still offers — a value it
 *  does not publish would be refused at the next run, and a pick on another
 *  runtime has nothing to carry over. */
function carried(next: Choice, before: Choice | null, runtime: Runtime | undefined, sets: Runtime["options"]): Choice {
    if (!before?.selections || before.runtimeId !== next.runtimeId || runtime?.id !== next.runtimeId || !sets) return next;
    const kept = offeredSelections(optionsFor(sets, chosenModel(runtime, next)), before.selections);
    return Object.keys(kept).length ? { ...next, selections: kept } : next;
}

/** A coding agent's options, under the model: effort, mode and the rest, as
 *  the agent named them. "Agent's default" is a real answer and the first
 *  one — it sends nothing, and the agent runs as its computer has it. Compact
 *  selects rather than rows: these are adjustments to the pick above, not
 *  picks of their own. */
function AgentOptionFields({ options, choice, disabled, onChange, className, unreadable }: {
    options: AgentOption[];
    choice: Choice;
    disabled: boolean;
    onChange: (next: Choice) => void;
    className?: string;
    /** The agent's options could not be read. Said, because nothing drawn
     *  reads as "this agent has no options", which is a different thing. */
    unreadable?: boolean;
}) {
    if (unreadable) {
        return (
            <p className="pick__note" role="status">
                <WarningIcon size={13} /> Couldn’t read this agent’s options, so its effort stays as its computer has it.
            </p>
        );
    }
    if (options.length === 0) return null;
    const selections = choice.selections ?? {};
    const choose = (key: string, value: string) => {
        const next = { ...selections };
        if (value) next[key] = value;
        else delete next[key];
        onChange(Object.keys(next).length ? { ...choice, selections: next } : { runtimeId: choice.runtimeId, model: choice.model });
    };
    return (
        <div className={"agentopts" + (className ? " " + className : "")}>
            {options.map((option) => (
                <label key={option.key} title={option.description || undefined}>
                    <span>{option.label}</span>
                    <select
                        value={selections[option.key] ?? ""}
                        disabled={disabled}
                        onChange={(event) => choose(option.key, event.target.value)}
                    >
                        <option value="">Agent’s default</option>
                        {option.choices.map((one) => (
                            <option key={one.value} value={one.value}>{one.label}</option>
                        ))}
                    </select>
                </label>
            ))}
        </div>
    );
}

/** What this teammate runs on, read once for the picker and for the line
 *  about it on About's overview: the same three queries, the same sentence. */
export function useRunsOn(podId: string, orgId: string) {
    const runtimes = useQuery({
        queryKey: ["runtimes", orgId],
        queryFn: () => source.listRuntimes(orgId),
        staleTime: 5 * 60_000,
    });
    const inherited = useQuery({
        queryKey: ["default-runtime", orgId],
        queryFn: () => source.defaultRuntime(orgId),
        staleTime: 5 * 60_000,
    });
    const current = useQuery({
        queryKey: ["pod-runtime", podId],
        queryFn: () => source.getPodRuntime(podId),
    });

    /* Retired runtimes are gone from the picker and stay readable in
       settings — offering one here would pin a teammate to something that
       cannot take a run. */
    const live = useMemo(() => (runtimes.data ?? []).filter((one) => !one.archived), [runtimes.data]);

    const choice = current.data ?? null;
    const chosen = choice ? live.find((one) => one.id === choice.runtimeId) : undefined;
    const inheritedName = describeChoice(live, inherited.data ?? null);
    const setup = runtimes.isSuccess ? modelSetupState(live, inherited.isSuccess ? inherited.data : undefined, choice) : null;

    const said = choice
        ? describeChoice(live, choice) || "Selected runtime unavailable"
        : setup
            ? "No model — pick one"
            : inheritedName
                ? "Organization default — " + inheritedName
                : "Organization default";

    return { runtimes, inherited, current, live, choice, chosen, inheritedName, setup, said };
}

/** Which model the teammate runs on, laid out for a page of its own: every model
 *  the organization offers, grouped by where it comes from, one row each and
 *  the chosen one marked — no dropdown to open first. The organization's list
 *  itself is edited in Settings → Models, which the foot links to. */
export function ModelChoice({ podId, orgId, orgName }: { podId: string; orgId: string; orgName: string }) {
    const queryClient = useQueryClient();
    const { runtimes, current, live, choice, chosen, inheritedName, setup } = useRunsOn(podId, orgId);
    const { options, sets, unreadable } = useAgentOptions(orgId, chosen, choice);
    const set = useMutation({
        mutationFn: (next: Choice | null) => source.setPodRuntime(podId, next),
        onSuccess: (_answer, next) => {
            queryClient.setQueryData(["pod-runtime", podId], next);
            void queryClient.invalidateQueries({ queryKey: ["pod-runtime", podId] });
        },
    });
    const rows = useMemo(() => rowsOf(live), [live]);

    if (current.isError || runtimes.isError) {
        return <p className="aboutpage__quiet" role="alert">Couldn’t load the models {orgName} has.</p>;
    }
    if (current.isPending || runtimes.isPending) return <p className="aboutpage__quiet">Loading…</p>;

    const pending = set.isPending ? set.variables : undefined;
    const isOn = (runtime: Runtime, row: Choice) => Boolean(choice) && choice!.runtimeId === runtime.id
        && (sameChoice(choice, row) || chosenModel(runtime, choice) === row.model);

    return (
        <div className="modelchoice">
            {setup && (
                <p className="pick__note" role="status">
                    <WarningIcon size={13} />
                    <span>
                        {setup === "none"
                            ? "No AI model is set up yet, so it cannot answer."
                            : "The organization default has no model behind it. Pick one below, or add one."}
                    </span>
                    <AddModelAction />
                </p>
            )}

            <div className="modelchoice__list" role="radiogroup" aria-label="Default model">
                <button
                    className="modelchoice__row modelchoice__row--default"
                    role="radio"
                    aria-checked={!choice}
                    disabled={set.isPending}
                    onClick={() => set.mutate(null)}
                >
                    <span className="modelchoice__radio" aria-hidden="true" />
                    <span className="modelchoice__mark"><SparkleIcon size={16} /></span>
                    <span className="modelchoice__body">
                        <span>Organization default</span>
                        <small>{inheritedName ? inheritedName + " today — follows " + orgName + " if that changes" : "Whatever " + orgName + " uses"}</small>
                    </span>
                    {pending === null && <span className="modelchoice__saving">Saving…</span>}
                </button>

                {live.map((runtime) => (
                    <div className="modelchoice__group" key={runtime.id} role="group"
                        aria-label={runtime.trouble ? runtime.name + " — " + runtime.trouble : runtime.name}>
                        <div className="modelchoice__label" aria-hidden="true">
                            <Mark runtime={runtime} />
                            <span>{runtime.name}</span>
                            {runtime.trouble && <em><WarningIcon size={12} /> {runtime.trouble}</em>}
                        </div>
                        {rows.filter((row) => row.runtime.id === runtime.id).map((row) => (
                            <button
                                className="modelchoice__row"
                                role="radio"
                                aria-checked={isOn(runtime, row.choice)}
                                key={row.choice.model || runtime.id}
                                disabled={set.isPending}
                                onClick={() => set.mutate(carried(row.choice, choice, chosen, sets))}
                            >
                                <span className="modelchoice__radio" aria-hidden="true" />
                                <span className="modelchoice__body">
                                    <span>{row.label}</span>
                                    {row.choice.model && !sameName(row.label, row.choice.model) && (
                                        <small className="modelchoice__id">{shortModel(row.choice.model)}</small>
                                    )}
                                </span>
                                {pending && sameChoice(pending, row.choice) && <span className="modelchoice__saving">Saving…</span>}
                            </button>
                        ))}
                        {/* Under the group it belongs to, so it reads as
                            part of this pick rather than a setting of its own. */}
                        {choice?.runtimeId === runtime.id && (
                            <AgentOptionFields
                                className="agentopts--group"
                                options={options}
                                choice={choice}
                                disabled={set.isPending}
                                onChange={(next) => set.mutate(next)}
                                unreadable={unreadable}
                            />
                        )}
                    </div>
                ))}
            </div>

            {choice && !live.some((one) => one.id === choice.runtimeId) && (
                <p className="pick__note">
                    <WarningIcon size={13} /> What it was pinned to is gone. Pick something else.
                </p>
            )}
            {set.isError && (
                <p className="pick__note" role="alert">
                    <WarningIcon size={13} />
                    <span>{saidAbout(set.error, "That could not be saved.")}</span>
                </p>
            )}

            {/* The list is the organization's: a key is bought once, for every
                teammate. Adding or removing one is Settings' job. */}
            <p className="modelchoice__foot">
                <span>These are the models {orgName} has set up.</span>
                <button className="linkish" onClick={() => openSettings("models")}>Add or manage models in Settings</button>
            </p>
        </div>
    );
}
