import { useMemo, useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma } from "../../../data/hooks";
import type { Org } from "../../../data/types";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useMayI } from "../access/hooks";
import { adminRoutes } from "../routes";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { ChoiceRow, Group, ReadProblem, Reading, Row, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { useRuntimeDetail, useRunsOn, useSetLemmaChoice } from "./hooks";
import { carried, chosenOptions, isPicked, pickRows, sameName, shownRows, withSelection } from "./model";
import { Link, PickRow, WithOrg } from "./parts";
import { sameChoice, shortModel, type Choice } from "@web/data/runtimes";

/** Changing what a Lemma runs on is changing the Lemma. */
const UPDATE_POD = "pod.update";

/** The model a Lemma thinks with (the web's `shell/runs-on.tsx`
 *  `ModelChoice`): following the organization's default, or one model of the
 *  organization's set for this Lemma alone, every row on the page with the
 *  chosen one ticked; a chosen coding agent's effort and mode under it; and
 *  what to do when there is nothing to run on. The list itself is the
 *  organization's, changed under Models. */
export function LemmaModelScreen({ podId }: PartProps) {
    const lemma = useLemma(podId);
    const part = lemmaPart("model");
    const name = lemma?.name ?? "This Lemma";
    const title = part?.title(name) ?? "Default model";
    const note = part?.note(name) ?? null;
    const back = lemma?.name ?? "Settings";
    return (
        <WithOrg title={title} note={note} back={back}>
            {(org) => <LemmaModel podId={podId} org={org} name={name} title={title} note={note} back={back} />}
        </WithOrg>
    );
}

function LemmaModel({ podId, org, name, title, note, back }: { podId: string; org: Org; name: string; title: string; note: string | null; back: string }) {
    const router = useRouter();
    const { runtimes, current, live, choice, chosen, inheritedName, setup } = useRunsOn(podId, org.id);
    /* A chosen coding agent's options are read off the agent on its own:
       they are whatever its computer publishes now, and move with the model. */
    const detail = useRuntimeDetail(org.id, chosen?.kind === "agent" && choice?.runtimeId === chosen.id ? chosen : undefined);
    const sets = detail.data?.options;
    const options = chosenOptions(chosen, choice, sets);
    const set = useSetLemmaChoice(podId);
    const may = useMayI(podId, UPDATE_POD);
    const locked = may.answered && !may.allowed;
    const [everyModel, setEveryModel] = useState<ReadonlySet<string>>(new Set());
    const rows = useMemo(() => pickRows(live), [live]);
    const openModels = () => router.push(adminRoutes.section("models") as never);

    if (current.isError || runtimes.isError) {
        const error = current.error ?? runtimes.error;
        return (
            <SettingsScreen title={title} note={note} back={back}>
                <ReadProblem
                    said={isForbidden(error) ? `You may not see what ${name} runs on.` : problemOf(error, `The models ${org.name} has could not be read.`)}
                    onRetry={() => { void current.refetch(); void runtimes.refetch(); }}
                />
            </SettingsScreen>
        );
    }
    if (current.isPending || runtimes.isPending) {
        return <SettingsScreen title={title} note={note} back={back}><Reading /></SettingsScreen>;
    }

    /* The pick being saved: null is "the organization's default". */
    const saving: Choice | null | undefined = set.isPending ? set.variables : undefined;
    const busy = set.isPending || locked;
    const choose = (next: Choice | null) => { set.reset(); set.mutate(next); };

    return (
        <SettingsScreen title={title} note={note} back={back}>
            {setup ? (
                <View style={{ gap: 6 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <View accessibilityLabel="Needs you" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} />
                        <Text size={16} weight="medium">{setup === "none" ? "No model is set up yet" : "The default has no model behind it"}</Text>
                    </View>
                    <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>
                        {setup === "none"
                            ? `So ${name} cannot answer. A key or a computer is added under Models.`
                            : `${name} follows ${org.name}'s default, and nothing is behind it, so the next message fails. Pick a model below, or add one under Models.`}
                    </Text>
                    <Link label="Open Models" onPress={openModels} />
                </View>
            ) : null}

            {locked ? <Say>You may not change what {name} runs on. Somebody who runs {name} can.</Say> : null}

            <Group>
                <PickRow
                    label="Organization default"
                    hint={inheritedName ? `${inheritedName} today. Follows ${org.name} if that changes.` : `Whatever ${org.name} uses`}
                    on={!choice}
                    saving={saving === null}
                    disabled={busy || !choice}
                    onPress={() => choose(null)}
                    last
                />
            </Group>

            {live.map((runtime) => {
                const own = rows.filter((row) => row.runtime.id === runtime.id);
                const cut = shownRows(own, everyModel.has(runtime.id), (row) => isPicked(runtime, choice, row.choice));
                const holds = choice?.runtimeId === runtime.id;
                const optionsHere = holds && (options.length > 0 || detail.isError);
                return (
                    <Group key={runtime.id} label={runtime.trouble ? `${runtime.name} · ${runtime.trouble}` : runtime.name}>
                        {cut.rows.map((row, index) => {
                            const on = isPicked(runtime, choice, row.choice);
                            return (
                                <PickRow
                                    key={row.choice.model || runtime.id}
                                    label={row.label}
                                    hint={row.choice.model && !sameName(row.label, row.choice.model) ? shortModel(row.choice.model) : null}
                                    on={on}
                                    saving={Boolean(saving) && sameChoice(saving ?? null, row.choice)}
                                    disabled={busy || on}
                                    onPress={() => choose(carried(row.choice, choice, chosen, sets))}
                                    last={index === cut.rows.length - 1 && !cut.hidden && !optionsHere}
                                />
                            );
                        })}
                        {cut.hidden ? (
                            <Row label="Show every model" role="button" onPress={() => setEveryModel((was) => new Set([...was, runtime.id]))} end={<View />} last={!optionsHere} />
                        ) : null}
                        {holds && detail.isError ? (
                            <Say>Its options could not be read, so its effort stays as its computer has it.</Say>
                        ) : holds && choice ? options.map((option, index) => (
                            <ChoiceRow
                                key={option.key}
                                label={option.label}
                                hint={option.description || null}
                                title={option.label}
                                choices={[{ value: "", label: "Agent's default" }, ...option.choices]}
                                value={choice.selections?.[option.key] ?? ""}
                                onPick={(value) => choose(withSelection(choice, option.key, value))}
                                disabled={busy}
                                last={index === options.length - 1}
                            />
                        )) : null}
                    </Group>
                );
            })}

            {chosen?.trouble ? <Say tone="bad">{chosen.trouble}. It answers again when that computer is back.</Say> : null}
            {choice && !chosen ? <Say tone="bad">What it was set to is gone. Pick something else.</Say> : null}
            {set.isError ? (
                <Say tone="bad">{isForbidden(set.error) ? `You may not change what ${name} runs on.` : problemOf(set.error, "That could not be saved. Try again.")}</Say>
            ) : null}

            <View style={{ gap: 2 }}>
                <Say>These are the models {org.name} has set up.</Say>
                <Link label="Add or manage models" onPress={openModels} />
            </View>
        </SettingsScreen>
    );
}
