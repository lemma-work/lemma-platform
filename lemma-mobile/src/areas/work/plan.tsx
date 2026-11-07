import { useMemo, useState } from "react";
import { TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useMe } from "../../data/hooks";
import { NEW_CHAT, routes } from "../../data/routes";
import { useSession } from "../../session/session";
import { Loading } from "../../ui/chrome";
import { Form } from "../../ui/form/form";
import type { FormField } from "../../ui/form/model";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { useFolder } from "../files/hooks";
import { filesRoutes } from "../files/routes";
import { AskSheet, Quiet } from "../tables/bits";
import { useAgents, usePlan, useSavePlan, useSchedules } from "./hooks";
import { addLine, draftOf, editLine, metaOf, removeLine, withMeta, type PlanMeta } from "./model";
import { Heading, Note, Trouble } from "./parts";
import { PLAN_PATH, PLAN_SECTIONS, planRequest, SECTION_TITLES, type Plan, type PlanLine, type PlanSection } from "./web/plan";

/** A Lemma's plan, `/plan.md`, as the `/o` frame edits it: what it owns and
 *  how, line by line, each with who wrote it. A save edits the file in place
 *  and refuses when it changed since it was read, so a line the Lemma (or
 *  anyone) wrote meanwhile is never written over. With no plan yet, a draft
 *  read off what it already runs, to save as it is or to have the Lemma
 *  write properly in a chat. */
export function PlanView({ podId, name, description }: { podId: string; name: string; description: string | null }) {
    const router = useRouter();
    const read = usePlan(podId);
    const save = useSavePlan(podId);
    const [note, setNote] = useState<string | null>(null);
    const [asking, setAsking] = useState(false);
    const plan = read.data;

    const write = (next: Plan, exists: boolean) => {
        setNote(null);
        save.mutate({ plan: next, exists }, {
            onSuccess: () => setNote("Saved to plan.md"),
            onError: (problem) => setNote(problem instanceof Error ? `Couldn’t save: ${problem.message}` : "Couldn’t save the plan."),
        });
    };
    const askToWrite = () => router.push(routes.chat(podId, NEW_CHAT, planRequest(description ?? "")) as never);

    return (
        <View>
            {plan === undefined && read.isError ? (
                <Trouble text="The plan couldn’t be read." onRetry={() => void read.refetch()} />
            ) : plan === undefined ? (
                <Loading label="Reading the plan" />
            ) : plan === null ? (
                <PlanDraft podId={podId} name={name} description={description} busy={save.isPending} onSave={(draft) => write(draft, false)} onAsk={askToWrite} />
            ) : (
                <PlanEditor key={podId} podId={podId} plan={plan} busy={save.isPending} onChange={(next) => write(next, true)} onAsk={() => setAsking(true)} name={name} />
            )}
            {note ? <Note tone={note.startsWith("Couldn") ? color.amber : color.ink3} style={{ paddingTop: 10 }}>{note}</Note> : null}
            <AskSheet open={asking} onClose={() => setAsking(false)} name={name} start="In the plan (plan.md), " onSend={(text) => router.push(routes.chat(podId, NEW_CHAT, text) as never)} />
        </View>
    );
}

/** How many lines of a drafted section show before the rest fold away: a
 *  draft read off thirteen schedules is a list of machinery, not a plan. */
const DRAFT_SHOWN = 3;

function PlanDraft({ podId, name, description, busy, onSave, onAsk }: { podId: string; name: string; description: string | null; busy: boolean; onSave: (plan: Plan) => void; onAsk: () => void }) {
    const me = useMe();
    const schedules = useSchedules(podId, name);
    const agents = useAgents(podId, name);
    const skills = useFolder(podId, "/skills");
    const ready = !schedules.isPending && !agents.isPending && !skills.isPending;
    const { draft, stamped } = useMemo(() => draftOf({
        description,
        schedules: schedules.data ?? [],
        agents: agents.data ?? [],
        skills: (skills.data?.pages.flatMap((page) => page.items) ?? []).map((item) => item.name.replace(/\.md$/i, "")),
    }, me.data?.firstName ?? me.data?.name ?? "You", new Date()), [description, schedules.data, agents.data, skills.data, me.data]);
    return (
        <View>
            <Text size={16} style={{ lineHeight: 22, paddingHorizontal: 4 }}>No plan yet. Here is a draft from what {name} already runs.</Text>
            <View style={{ flexDirection: "row", gap: 8, paddingTop: 12, paddingHorizontal: 4 }}>
                <Button title="Save as the plan" busy={busy} disabled={!ready} onPress={() => onSave(stamped)} />
                <Button title={`Ask ${name} to write it`} kind="outline" disabled={busy} onPress={onAsk} />
            </View>
            {!ready ? <Loading label="Reading what it runs" /> : PLAN_SECTIONS.map((section) => <DraftSection key={section} title={SECTION_TITLES[section]} lines={draft.sections[section]} />)}
        </View>
    );
}

function DraftSection({ title, lines }: { title: string; lines: readonly PlanLine[] }) {
    const [all, setAll] = useState(false);
    return (
        <View>
            <Heading>{title}</Heading>
            {lines.length === 0 ? <Note tone={color.ink3}>Nothing yet</Note> : (all ? lines : lines.slice(0, DRAFT_SHOWN)).map((line, index) => <Bullet key={index} text={line.text} />)}
            {lines.length > DRAFT_SHOWN ? <Quiet text={all ? "Show fewer" : `${lines.length - DRAFT_SHOWN} more`} onPress={() => setAll((was) => !was)} /> : null}
        </View>
    );
}

function Bullet({ text, by }: { text: string; by?: string }) {
    return (
        <View style={{ flexDirection: "row", gap: 10, paddingVertical: 9, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <View style={{ width: 5, height: 5, borderRadius: 3, backgroundColor: color.ink3, marginTop: 8 }} />
            <View style={{ flex: 1, gap: 2 }}>
                <Text size={15.5} style={{ lineHeight: 21 }}>{text}</Text>
                {by ? <Text size={12.5} tone={color.ink3}>{by}</Text> : null}
            </View>
        </View>
    );
}

/** The measure form's fields, drawn by the app's own form. */
const META_FIELDS: FormField[] = [
    { name: "outcome", label: "What it owns", description: "A bar it holds every week, or a finish line by a date.", required: false, kind: "text", options: [], readOnly: false, reference: null, integer: false },
    { name: "kind", label: "Kind", description: null, required: false, kind: "select", options: [{ label: "A bar every week", value: "week" }, { label: "A finish line by a date", value: "date" }], readOnly: false, reference: null, integer: false },
    { name: "target", label: "Target", description: "As you would say it: 90%, 40.", required: false, kind: "text", options: [], readOnly: false, reference: null, integer: false },
    { name: "unit", label: "Unit", description: null, required: false, kind: "select", options: [{ label: "A count", value: "" }, { label: "A share (%)", value: "%" }], readOnly: false, reference: null, integer: false },
    { name: "due", label: "Due", description: null, required: false, kind: "date", options: [], readOnly: false, reference: null, integer: false },
    { name: "measure", label: "Measure", description: "One read-only SQL query over this Lemma’s tables that answers a single number.", required: false, kind: "textarea", options: [], readOnly: false, reference: null, integer: false },
];

function PlanEditor({ podId, plan, busy, onChange, onAsk, name }: { podId: string; plan: Plan; busy: boolean; onChange: (plan: Plan) => void; onAsk: () => void; name: string }) {
    const router = useRouter();
    const { mode } = useSession();
    const me = useMe();
    const by = me.data?.firstName ?? me.data?.name ?? "You";
    const [editing, setEditing] = useState<{ section: PlanSection; index: number; text: string } | null>(null);
    const [adding, setAdding] = useState<Record<PlanSection, string>>({ must: "", how: "", who: "", never: "" });
    const [measure, setMeasure] = useState<PlanMeta | null>(null);

    const commit = () => {
        if (!editing) return;
        const next = editLine(plan, editing.section, editing.index, editing.text);
        setEditing(null);
        if (next) onChange(next);
    };
    const add = (section: PlanSection) => {
        const next = addLine(plan, section, adding[section], by, new Date());
        if (next === plan) return;
        onChange(next);
        setAdding((was) => ({ ...was, [section]: "" }));
    };

    return (
        <View>
            {PLAN_SECTIONS.map((section, sectionIndex) => (
                <View key={section}>
                    <Heading first={sectionIndex === 0}>{SECTION_TITLES[section]}</Heading>
                    {plan.sections[section].map((line, index) => (
                        editing && editing.section === section && editing.index === index ? (
                            <View key={index} style={{ gap: 8, paddingVertical: 8, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
                                <TextInput
                                    accessibilityLabel="Edit the line"
                                    autoFocus
                                    multiline
                                    value={editing.text}
                                    onChangeText={(text) => setEditing({ ...editing, text })}
                                    style={{ minHeight: TOUCH, borderWidth: 1, borderColor: color.line2, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, textAlignVertical: "top", fontFamily: font.regular, fontSize: 15.5, lineHeight: 21, color: color.ink }}
                                />
                                <View style={{ flexDirection: "row", gap: 8 }}>
                                    <Button title="Done" height={36} size={14} onPress={commit} />
                                    <Button title="Remove" kind="outline" height={36} size={14} onPress={() => { setEditing(null); onChange(removeLine(plan, section, index)); }} />
                                    <Button title="Cancel" kind="quiet" height={36} size={14} onPress={() => setEditing(null)} />
                                </View>
                            </View>
                        ) : (
                            <Press key={index} label={`${line.text}. Edit`} disabled={busy} onPress={() => setEditing({ section, index, text: line.text })}>
                                <Bullet text={line.text} by={line.by ? `${line.by}${line.on ? " · " + line.on : ""}` : undefined} />
                            </Press>
                        )
                    ))}
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 4, minHeight: TOUCH }}>
                        <Icon name="plus" size={15} tone={color.ink3} />
                        <TextInput
                            accessibilityLabel={`Add to ${SECTION_TITLES[section]}`}
                            value={adding[section]}
                            editable={!busy}
                            placeholder="Add a line"
                            placeholderTextColor={color.ink3}
                            returnKeyType="done"
                            onChangeText={(text) => setAdding((was) => ({ ...was, [section]: text }))}
                            onSubmitEditing={() => add(section)}
                            style={{ flex: 1, minHeight: TOUCH, fontFamily: font.regular, fontSize: 15.5, color: color.ink, padding: 0 }}
                        />
                        {adding[section].trim() ? <Button title="Add" height={34} size={14} disabled={busy} onPress={() => add(section)} /> : null}
                    </View>
                </View>
            ))}

            <View style={{ paddingTop: 14, gap: 2 }}>
                <Quiet text={plan.measure ? "The measure and target" : "Add a measure, so what it owns gets a number"} tone={color.ink} onPress={() => setMeasure(measure ? null : metaOf(plan))} />
                {measure ? (
                    <View style={{ gap: 14, paddingHorizontal: 4, paddingTop: 6, paddingBottom: 6 }}>
                        <Form
                            fields={META_FIELDS.filter((field) => field.name !== "due" || measure.kind === "date")}
                            values={measure as unknown as Record<string, unknown>}
                            onChange={(field, value) => setMeasure({ ...measure, [field]: field === "kind" ? (value === "date" ? "date" : "week") : String(value ?? "") })}
                        />
                        <View style={{ flexDirection: "row", gap: 8 }}>
                            <Button title="Save" busy={busy} onPress={() => { onChange(withMeta(plan, measure)); setMeasure(null); }} />
                            <Button title="Cancel" kind="outline" onPress={() => setMeasure(null)} />
                        </View>
                    </View>
                ) : null}
                {mode === "live" ? <Quiet text="Open plan.md" onPress={() => router.push(filesRoutes.file(podId, PLAN_PATH) as never)} /> : null}
                <Quiet text={`Ask ${name} about the plan`} onPress={onAsk} />
            </View>
        </View>
    );
}
