import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    source,
    agentDefaultLabel,
    agentLogo,
    chosenModel,
    describeChoice,
    sameChoice,
    saidAbout,
    shortModel,
    type Choice,
    type Runtime,
} from "@/data";
import { CheckIcon, ChevronDownIcon, KeyIcon, LemmaMark, SparkleIcon, TerminalIcon, WarningIcon } from "@/ui/icons";
import { usePicker } from "@/ui/picker";
import { SetUpAiModelLink } from "@/desktop/set-up-on-this-mac";
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

export function RunsOn({ podId, orgId }: { podId: string; orgId: string }) {
    const queryClient = useQueryClient();
    const { open, setOpen, wrap, menuClass } = usePicker();

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

    const set = useMutation({
        mutationFn: (choice: Choice | null) => source.setPodRuntime(podId, choice),
        onSuccess: (_answer, choice) => {
            queryClient.setQueryData(["pod-runtime", podId], choice);
            void queryClient.invalidateQueries({ queryKey: ["pod-runtime", podId] });
        },
    });

    /* Retired runtimes are gone from the picker and stay readable in
       settings — offering one here would pin a teammate to something that
       cannot take a run. */
    const live = useMemo(() => (runtimes.data ?? []).filter((one) => !one.archived), [runtimes.data]);
    const rows = useMemo(() => rowsOf(live), [live]);

    const choice = current.data ?? null;
    const chosen = choice ? live.find((one) => one.id === choice.runtimeId) : undefined;
    const inheritedName = describeChoice(live, inherited.data ?? null);
    const setup = runtimes.isSuccess ? modelSetupState(live, inherited.isSuccess ? inherited.data : undefined, choice) : null;

    if (current.isError || runtimes.isError) {
        return <p className="empty-row">Couldn’t load available runtimes.</p>;
    }

    const said = choice
        ? describeChoice(live, choice) || "Selected runtime unavailable"
        : setup === "none"
            ? "No AI model set up yet"
            : inheritedName
                ? "Organization default — " + inheritedName
                : "Organization default";

    return (
        <div className="pick" ref={wrap}>
            <button
                className="pick__face"
                aria-expanded={open}
                aria-haspopup="menu"
                disabled={runtimes.isPending || current.isPending}
                onClick={() => setOpen((was) => !was)}
            >
                <span className="pick__mark"><Mark runtime={chosen} /></span>
                <span className="pick__said">{current.isPending ? "Reading…" : said}</span>
                <ChevronDownIcon size={14} />
            </button>

            {/* A pin to something unreachable is the one case worth saying out
                loud here: it is not broken *now*, and it will be the moment
                somebody asks. */}
            {chosen?.trouble && (
                <p className="pick__note">
                    <WarningIcon size={13} /> {chosen.trouble}. It answers again when that computer is back.
                </p>
            )}
            {choice && !chosen && !runtimes.isPending && (
                <p className="pick__note">
                    <WarningIcon size={13} /> What this teammate was pinned to is gone. Pick something else.
                </p>
            )}
            {/* Said before anyone sends a message, rather than as the error
                the first message would come back with. The link is drawn only
                inside the Lemma app on the machine it runs on; elsewhere the
                sentence names the page that fixes it. */}
            {setup === "none" && (
                <p className="pick__note" role="status">
                    <WarningIcon size={13} />
                    <span>No AI model is set up yet, so this teammate cannot answer. Add a provider in Organization → Models.</span>
                    <SetUpAiModelLink />
                </p>
            )}
            {setup === "default-missing" && (
                <p className="pick__note" role="status">
                    <WarningIcon size={13} />
                    <span>The organization default has no AI model behind it. Pick a model here, or set one up.</span>
                    <SetUpAiModelLink />
                </p>
            )}

            {open && (
                <div className={menuClass} role="menu">
                    <button
                        className="pick__row"
                        role="menuitem"
                        onClick={() => { set.mutate(null); setOpen(false); }}
                    >
                        <span className="pick__tick">{!choice && <CheckIcon size={13} />}</span>
                        <span className="pick__body">
                            <span>Organization default</span>
                            {inheritedName && <em>{inheritedName} today</em>}
                        </span>
                    </button>

                    {live.length === 0 && (
                        <p className="pick__empty">
                            No AI model is set up yet. Organization → Models is where a key or a computer is added.
                            <SetUpAiModelLink />
                        </p>
                    )}

                    {live.map((runtime) => (
                        /* The heading is decoration once the group carries the
                           name — but the trouble printed on it is not, so that
                           travels into the name rather than being hidden with
                           it. */
                        <div
                            className="runson__group"
                            key={runtime.id}
                            role="group"
                            aria-label={runtime.trouble ? runtime.name + " — " + runtime.trouble : runtime.name}
                        >
                            <div className="runson__label" aria-hidden="true">
                                <Mark runtime={runtime} />
                                <span>{runtime.name}</span>
                                {runtime.trouble && <em>{runtime.trouble}</em>}
                            </div>
                            {rows.filter((row) => row.runtime.id === runtime.id).map((row) => {
                                /* An open model and the model it resolves to
                                   are the same pick, so both draw as chosen. */
                                const on = Boolean(choice) && choice!.runtimeId === runtime.id
                                    && (sameChoice(choice, row.choice) || chosenModel(runtime, choice) === row.choice.model);
                                return (
                                    <button
                                        className="pick__row"
                                        role="menuitem"
                                        key={row.choice.model || runtime.id}
                                        onClick={() => { set.mutate(row.choice); setOpen(false); }}
                                    >
                                        <span className="pick__tick">{on && <CheckIcon size={13} />}</span>
                                        <span className="pick__body">
                                            <span>{row.label}</span>
                                            {/* The id, but only when it is not
                                                just the label with the
                                                punctuation put back — "Claude
                                                Sonnet 5" over
                                                "claude-sonnet-5" is the same
                                                thing said twice. */}
                                            {row.choice.model && !sameName(row.label, row.choice.model) && (
                                                <em>{shortModel(row.choice.model)}</em>
                                            )}
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    ))}
                </div>
            )}

            {/* Same as the door below it: a refusal to pin a teammate to a
                model is a rule the platform can state and this app can only
                guess at. */}
            {set.isError && (
                <p className="pick__note" role="alert">
                    <WarningIcon size={13} />
                    <span>{saidAbout(set.error, "That could not be saved.")}</span>
                </p>
            )}
        </div>
    );
}
