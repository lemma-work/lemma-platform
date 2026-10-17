"use client";

import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { shortModel, type Choice } from "@/data/runtimes";
import { useRunsOn } from "@/shell/runs-on";

/* The save of a conversation's model, by a key the pane can watch: a first
   message sent while it is still in the air would start a run on the model
   the conversation had before. */
const SAVING_MODEL = ["conversation-runtime", "save"];

/** Whether a model change is still being saved, so the pane can hold the
 *  send until the conversation runs on what was picked. */
export function useSavingModel(): boolean {
    return useIsMutating({ mutationKey: SAVING_MODEL }) > 0;
}

/** The model this conversation answers on, beside the paperclip.
 *
 *  One row per model the teammate would otherwise run, and a first row that
 *  says what "nothing chosen" currently means — a picker whose default is an
 *  unnamed grey option makes people choose a model just to find out what they
 *  already had. Choosing the first row sends no override at all, so the
 *  conversation runs exactly as it did before this existed.
 *
 *  The choice is the conversation's, not the teammate's: a long thread about
 *  one hard question can want the bigger model without moving every other
 *  conversation in the pod onto it. Once the conversation has been answered,
 *  the row is a label rather than a picker — the runs it started already ran
 *  on the model they ran on, and the control would only offer to change the
 *  history it cannot. */
export function ModelPicker({
    podId,
    orgId,
    conversationId,
    locked,
    picked,
    onPicked,
}: {
    podId: string;
    orgId: string;
    /** Null while the conversation is still only a draft: the choice is then
     *  held by the pane and sent with the conversation it creates. */
    conversationId: string | null;
    /** A message has been sent: show what it runs on, offer no change. */
    locked: boolean;
    /** The choice made before the conversation existed. */
    picked: Choice | null;
    onPicked: (choice: Choice | null) => void;
}) {
    const queryClient = useQueryClient();
    const { live, choice, inherited, runtimes } = useRunsOn(podId, orgId);

    /* The conversation's own pin, when it has one. Read rather than kept in
       state, because it is the field a run copies and it can be set on another
       device. */
    const pin = useQuery({
        queryKey: ["conversation-runtime", conversationId],
        queryFn: () => source.getConversationRuntime(podId, conversationId as string),
        enabled: Boolean(conversationId),
    });

    const set = useMutation({
        mutationKey: SAVING_MODEL,
        mutationFn: (next: Choice | null) =>
            source.setConversationRuntime(podId, conversationId as string, next),
        onSuccess: (_answer, next) => {
            queryClient.setQueryData(["conversation-runtime", conversationId], next);
            void queryClient.invalidateQueries({ queryKey: ["conversation-runtime", conversationId] });
        },
    });

    /* What it runs on when nothing is chosen: the teammate's own pick if it has
       one, otherwise the organization's. This is the runtime whose models are
       worth offering — a model from a key this conversation would not reach is
       a choice that changes nothing. */
    const fallback = choice ?? inherited.data ?? null;
    const runtime = fallback ? live.find((one) => one.id === fallback.runtimeId) : undefined;
    const models = runtime?.models ?? [];
    /* The model the default resolves to, by the name the options beside it use:
       a runtime that pins nothing runs the model its profile names, and a
       conversation that overrides nothing runs what the teammate runs. Naming
       the runtime here instead ("Claude Code · Opus") read as a second thing
       being chosen and, on a phone, cut the model's own name off the end of the
       one row that explains the default. */
    const resolved = fallback?.model || runtime?.defaultModel || "";
    const defaultLabel = "Default (" + (shortModel(resolved) || "no model set") + ")";

    const chosen = conversationId ? pin.data ?? null : picked;
    const value = chosen?.model ?? "";
    const chosenLabel = chosen
        ? models.find((model) => model.name === chosen.model)?.label ?? chosen.model
        : "";

    /* Answered: a label, not a control. Shown for a saved pin even when the
       teammate's runtime no longer offers a choice, because that pin is still
       what every later turn of this conversation copies. */
    if (locked && (chosen || models.length >= 2)) {
        return (
            <span className="modelpick modelpick--set" title={chosen ? "This conversation runs on " + chosenLabel : defaultLabel}>
                {chosen ? chosenLabel : defaultLabel}
            </span>
        );
    }

    /* Nothing to choose between: one model, or none at all. The row would be a
       control that cannot change anything, and the missing-model case is
       already said, louder, by the run that fails. */
    if (locked || models.length < 2) return null;

    const choose = (model: string) => {
        const next = model ? { runtimeId: runtime?.id ?? "", model } : null;
        if (conversationId) set.mutate(next);
        else onPicked(next);
    };

    return (
        <label className="modelpick" title="Which model this conversation answers on">
            <select
                className="modelpick__select"
                value={value}
                disabled={set.isPending || runtimes.isPending}
                aria-label="Model for this conversation"
                onChange={(event) => choose(event.target.value)}
            >
                <option value="">{defaultLabel}</option>
                {models.map((model) => (
                    <option key={model.name} value={model.name}>{model.label}</option>
                ))}
            </select>
            {/* The select snaps back to what was saved, which on its own reads
                as the click not having landed. Cleared by the next choice,
                which starts a fresh save. */}
            {set.isError ? (
                <span className="modelpick__error" role="alert" title={set.error instanceof Error ? set.error.message : undefined}>
                    Not saved
                </span>
            ) : null}
        </label>
    );
}
