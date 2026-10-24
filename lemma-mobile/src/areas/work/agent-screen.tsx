import { useMemo, useState, type ReactNode } from "react";
import { Switch, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useLemma } from "../../data/hooks";
import { routes } from "../../data/routes";
import { displayAgentName } from "@web/data/agent-names";
import { FixedAskBox } from "../../ui/ask-box";
import { Loading } from "../../ui/chrome";
import { LemmaFace } from "../../ui/face";
import { Form } from "../../ui/form/form";
import type { FormField } from "../../ui/form/model";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, font } from "../../ui/tokens";
import { adminRoutes } from "../admin/routes";
import { wakeFor, wakeRequest } from "@web/docpages/comments/wake";
import { ConfirmSheet, Problem, Quiet } from "../tables/bits";
import { useAgent, useAgentChats, useAgentWrites, useRuntime, useRuntimes, useSchedules, useScheduleWrites, useSurfaces, useWorkflows, useWorkSource } from "./hooks";
import { accessOf, platformWord, sayPermissions, stampAt, surfacesFor, surfaceState, usedBy } from "./model";
import { Heading, Line, Note, Prose, Tags, Trouble } from "./parts";
import { workRoutes } from "./routes";
import { Screen, Title } from "./screen";
import { AGENT_EDIT, AGENT_REMOVE, AGENT_RUN, agentProblems, draftOfAgent, hasAgentChanges, may, REASONING_EFFORTS, runtimeLine, type AgentDetail, type AgentDraft } from "@web/data/agents";
import { capabilityList, grantedToolsets } from "@web/stage/colleagues";
import { chosenModel, optionsFor } from "@web/data/runtimes";
import { humanizeName } from "@web/schedule/schedules";

/** One agent (`space/agent-page.tsx`, with `stage/agents-view.tsx`'s editor):
 *  who it is and what it is for; a box to ask it something, and the chats
 *  with it; then what it may do, where people reach it, what it runs on, and
 *  what hands it work. Edit and Delete are offered as the server allows
 *  (`allowed_actions`, and never on the Lemma's own agent, which the API
 *  refuses either way). */
export function AgentScreen() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; name: string; back?: string }>();
    const podId = String(params.podId);
    const name = String(params.name);
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "the Lemma";
    const agent = useAgent(podId, name, lemmaName);
    const writes = useAgentWrites(podId, name);
    const surfaces = useSurfaces(podId);
    const [editing, setEditing] = useState(false);
    const [removing, setRemoving] = useState(false);
    const detail = agent.data;
    const calling = detail?.label ?? displayAgentName(name);
    const back = params.back ? String(params.back) : lemmaName;

    if (!detail) {
        return (
            <Screen podId={podId} back={back}>
                {agent.isError ? (
                    <Trouble text={(agent.error as { statusCode?: number }).statusCode === 403 ? `You may not open ${calling}.` : `Couldn’t read ${calling}.`} onRetry={() => void agent.refetch()} />
                ) : <Loading label={`Opening ${calling}`} />}
            </Screen>
        );
    }

    const lost = surfacesFor(surfaces.data ?? [], detail).filter((one) => one.active);
    const face = detail.front ? <LemmaFace id={podId} icon={lemma?.iconUrl} size={44} /> : <LemmaFace id={podId + ":" + detail.name} icon={detail.iconUrl} size={44} />;
    return (
        <Screen podId={podId} back={back} onRefresh={() => agent.refetch()}>
            <Title glyph={face} title={detail.label}>
                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{detail.description || detail.blurb || (detail.front ? `${lemmaName} itself: the agent that answers here.` : "No description written.")}</Text>
                <Tags tags={[
                    ...(detail.front ? [{ text: "Answers here" }] : []),
                    ...(detail.visibility === "RESTRICTED" ? [{ text: "Restricted" }] : []),
                    ...(detail.takesInput ? [{ text: "Called by workflows" }] : []),
                ]} />
                {!editing && (may(detail, AGENT_EDIT) || (may(detail, AGENT_REMOVE) && !detail.front)) ? (
                    <View style={{ flexDirection: "row", gap: 8, paddingTop: 4 }}>
                        {may(detail, AGENT_EDIT) ? <Button title="Edit" kind="outline" onPress={() => setEditing(true)} /> : null}
                        {may(detail, AGENT_REMOVE) && !detail.front ? <Button title="Delete" kind="outline" onPress={() => setRemoving(true)} /> : null}
                    </View>
                ) : null}
            </Title>

            {editing ? (
                <Editor key={detail.name} podId={podId} detail={detail} onDone={() => setEditing(false)} />
            ) : (
                <>
                    <Ask podId={podId} detail={detail} />
                    <Chats podId={podId} detail={detail} />
                    <Instructions detail={detail} />
                    <Abilities detail={detail} />
                    <Reach podId={podId} detail={detail} query={surfaces} />
                    <Model detail={detail} />
                    <Comments podId={podId} detail={detail} lemmaName={lemmaName} />
                    <UsedBy podId={podId} detail={detail} lemmaName={lemmaName} />
                    <Access detail={detail} lemmaName={lemmaName} />
                </>
            )}

            <ConfirmSheet
                open={removing}
                onClose={() => { setRemoving(false); writes.remove.reset(); }}
                title={`Delete ${detail.label}`}
                say={`Delete ${detail.label}? Its instruction goes with it, anything holding a token for it stops working at once, and ${detail.label} can no longer be handed work.` + (lost.length ? ` ${lost.length === 1 ? "This address stops answering" : "These addresses stop answering"}: ${lost.map((one) => one.handle + " (" + platformWord(one.platform) + ")").join(", ")}.` : "")}
                yes={`Delete ${detail.label}`}
                busy={writes.remove.isPending}
                onYes={() => writes.remove.mutate(undefined, { onSuccess: () => { setRemoving(false); router.back(); } })}
            />
            {writes.remove.isError ? <Problem text={writes.remove.error instanceof Error ? writes.remove.error.message : "That was not deleted."} onDismiss={() => writes.remove.reset()} /> : null}
        </Screen>
    );
}

function Block({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
    return (
        <View>
            <Heading right={right}>{title}</Heading>
            <View style={{ paddingHorizontal: 4 }}>{children}</View>
        </View>
    );
}

/* ── talking to it ───────────────────────────────────────────────────── */

/** The box: sending starts a chat with this agent (made with its name, so it
 *  is this agent answering) and opens it. */
function Ask({ podId, detail }: { podId: string; detail: AgentDetail }) {
    const router = useRouter();
    const source = useWorkSource();
    const [sending, setSending] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    if (detail.takesInput) return <Note style={{ paddingTop: 12 }}>{detail.label} is called with arguments, by a workflow or another agent, rather than talked to.</Note>;
    if (!may(detail, AGENT_RUN) && detail.actions.length > 0) return <Note style={{ paddingTop: 12 }}>You can read about {detail.label}, but not talk to it.</Note>;
    if (detail.front) {
        return <View style={{ paddingTop: 14 }}><FixedAskBox name={detail.label} onSend={(text) => router.push(routes.chat(podId, "new", text) as never)} /></View>;
    }
    const send = async (text: string) => {
        setSending(true);
        setProblem(null);
        try {
            const id = await source.talkTo(podId, detail.name, text);
            router.push(routes.chat(podId, id) as never);
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "That was not sent.");
        } finally {
            setSending(false);
        }
    };
    return (
        <View style={{ paddingTop: 14, gap: 6, opacity: sending ? 0.5 : 1 }}>
            <FixedAskBox name={detail.label} onSend={(text) => { if (!sending) void send(text); }} />
            {problem ? <Problem text={problem} onDismiss={() => setProblem(null)} /> : null}
        </View>
    );
}

const CHATS_SHOWN = 6;

function Chats({ podId, detail }: { podId: string; detail: AgentDetail }) {
    const router = useRouter();
    const [all, setAll] = useState(false);
    const chats = useAgentChats(podId, detail, !detail.takesInput);
    if (detail.takesInput) return null;
    const rows = chats.data ?? [];
    return (
        <View>
            <Heading count={rows.length}>Chats with it</Heading>
            {chats.isPending ? <Loading /> : chats.isError ? <Trouble text="Couldn’t read the chats." onRetry={() => void chats.refetch()} /> : rows.length === 0 ? (
                <Note>You haven’t talked to {detail.label} yet.</Note>
            ) : (all ? rows : rows.slice(0, CHATS_SHOWN)).map((row) => (
                <Line key={row.id} glyph={<Icon name="chat" size={16} tone={color.ink2} />} title={row.title} when={stampAt(row.at)} onPress={() => router.push(routes.chat(podId, row.id) as never)} />
            ))}
            {rows.length > CHATS_SHOWN ? <Quiet text={all ? "Show fewer" : `Show all ${rows.length}`} onPress={() => setAll((was) => !was)} /> : null}
        </View>
    );
}

/* ── what it is made of ──────────────────────────────────────────────── */

function Instructions({ detail }: { detail: AgentDetail }) {
    return (
        <Block title="Instructions">
            {detail.front && !detail.instruction ? <Note style={{ paddingHorizontal: 0 }}>Follows its own memory and skills.</Note>
                : detail.instruction ? <Prose text={detail.instruction} />
                : <Note style={{ paddingHorizontal: 0 }}>None written.</Note>}
        </Block>
    );
}

function Abilities({ detail }: { detail: AgentDetail }) {
    const list = capabilityList(grantedToolsets(detail.toolsets, detail.front));
    return (
        <Block title="Can">
            {list.length === 0 ? <Note style={{ paddingHorizontal: 0 }}>Only read and reply: no tools.</Note> : list.map((one) => (
                <Text key={one.code} size={14.5} style={{ lineHeight: 20.5, paddingVertical: 3 }}>{one.word}<Text size={13.5} tone={color.ink3}>{"  "}{one.says}</Text></Text>
            ))}
            {detail.grants.map((grant) => (
                <Text key={grant.resource + grant.name} size={14} style={{ lineHeight: 20, paddingVertical: 3 }}>
                    {grant.name}
                    <Text size={13} tone={color.ink3}>{"  " + grant.resource.toLowerCase().replace(/_/g, " ") + " · " + sayPermissions(grant.permissions)}</Text>
                </Text>
            ))}
        </Block>
    );
}

function Reach({ podId, detail, query }: { podId: string; detail: AgentDetail; query: ReturnType<typeof useSurfaces> }) {
    const router = useRouter();
    const own = surfacesFor(query.data ?? [], detail);
    return (
        <Block title="Reach it on" right={own.length ? <Quiet text="Manage" onPress={() => router.push(adminRoutes.lemma(podId) as never)} /> : undefined}>
            {query.isPending ? <Loading /> : own.length === 0 ? <Note style={{ paddingHorizontal: 0 }}>Only here, in the app.</Note> : own.map((surface) => (
                <Text key={surface.id} size={14.5} style={{ lineHeight: 20.5, paddingVertical: 3 }}>
                    {platformWord(surface.platform)}
                    <Text size={13} tone={color.ink3}>{surface.handle ? "  " + surface.handle : ""}</Text>
                    <Text size={13} tone={surface.active ? color.ink3 : color.amber}>{"  · " + surfaceState(surface.status, surface.active)}</Text>
                </Text>
            ))}
        </Block>
    );
}

function Model({ detail }: { detail: AgentDetail }) {
    const runtimes = useRuntimes(Boolean(detail.runtime));
    const line = runtimeLine(detail, runtimes.data);
    return (
        <Block title="Model">
            <Text size={14.5} style={{ lineHeight: 20.5 }}>{line || "The default model here"}</Text>
            {detail.input ? <Text size={13.5} tone={color.ink2} style={{ paddingTop: 4 }}>Takes: {detail.input.opaque ? "structured input" : detail.input.fields.join(", ")}</Text> : null}
            {detail.output ? <Text size={13.5} tone={color.ink2} style={{ paddingTop: 2 }}>Returns: {detail.output.opaque ? "structured output" : detail.output.fields.join(", ")}</Text> : null}
        </Block>
    );
}

/** Whether a comment that mentions it wakes it: a schedule on the comments
 *  table (`docpages/comments/wake.ts`). */
function Comments({ podId, detail, lemmaName }: { podId: string; detail: AgentDetail; lemmaName: string }) {
    const schedules = useSchedules(podId, lemmaName);
    const writes = useScheduleWrites(podId);
    const key = detail.front ? "POD_DEFAULT" : detail.name;
    const job = wakeFor(schedules.data ?? [], key);
    const busy = writes.create.isPending || writes.remove.isPending;
    const failed = writes.create.error ?? writes.remove.error;
    if (detail.takesInput) return null;
    const flip = () => {
        if (job) writes.remove.mutate(job.id);
        else writes.create.mutate(wakeRequest(key, detail.label));
    };
    return (
        <Block title="Comments">
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 44 }}>
                <Text size={14.5} style={{ flex: 1, lineHeight: 20 }}>Answers comments that mention @{detail.label}</Text>
                <Switch
                    accessibilityLabel={`Answers comments that mention ${detail.label}`}
                    value={Boolean(job)}
                    disabled={busy || schedules.isPending}
                    onValueChange={flip}
                    trackColor={{ false: color.track, true: color.ink }}
                    thumbColor={job ? color.paper : color.ink3}
                    ios_backgroundColor={color.track}
                />
            </View>
            <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{job ? "Mention it on any page and it reads the page, does what you ask, and replies in the thread." : "Off: a mention in a comment is only a mention."}</Text>
            {failed ? <Text size={13} tone={color.amber} style={{ paddingTop: 4 }}>{(failed as { statusCode?: number }).statusCode === 403 ? "Only an editor here can change this." : "Couldn’t change that."}</Text> : null}
        </Block>
    );
}

function UsedBy({ podId, detail, lemmaName }: { podId: string; detail: AgentDetail; lemmaName: string }) {
    const router = useRouter();
    const schedules = useSchedules(podId, lemmaName);
    const flows = useWorkflows(podId);
    const used = usedBy(detail, schedules.data ?? [], flows.data ?? []);
    return (
        <View>
            <Heading>Used by</Heading>
            {used.jobs.length === 0 && used.flows.length === 0 ? <Note>No schedule or workflow hands it work.</Note> : null}
            {used.jobs.map((job) => <Line key={"s" + job.id} glyph={<Icon name="clock" size={16} tone={color.ink2} />} title={job.title} quiet={job.trigger} onPress={() => router.push(workRoutes.schedule(podId, job.id) as never)} />)}
            {used.flows.map((flow) => <Line key={"w" + flow.id} glyph={<Icon name="automation" size={16} tone={color.ink2} />} title={humanizeName(flow.name)} quiet="Workflow" onPress={() => router.push(workRoutes.workflow(podId, flow.name) as never)} />)}
        </View>
    );
}

function Access({ detail, lemmaName }: { detail: AgentDetail; lemmaName: string }) {
    const said = accessOf(detail, lemmaName);
    return (
        <Block title="Access">
            <Text size={14.5} style={{ lineHeight: 20.5 }}>{said.who}</Text>
            <Text size={13.5} tone={color.ink2} style={{ lineHeight: 19, paddingTop: 2 }}>{said.you}</Text>
        </Block>
    );
}

/* ── editing ─────────────────────────────────────────────────────────── */

/** The fields worth editing by hand (`stage/agents-view.tsx`'s Editor):
 *  description and instruction; and, on a pinned model, its reasoning effort
 *  and output limit, or a coding agent's own options. Only what changed is
 *  sent. Toolsets, visibility and the model are left out: each has a
 *  consequence outside this screen, and are set by asking for them. */
function Editor({ podId, detail, onDone }: { podId: string; detail: AgentDetail; onDone: () => void }) {
    const writes = useAgentWrites(podId, detail.name);
    const pinned = detail.runtime;
    const runtimes = useRuntimes(Boolean(pinned));
    const runtime = runtimes.data?.find((one) => one.id === pinned?.profile);
    const model = runtime?.models.find((one) => one.name === (pinned?.model || runtime.defaultModel));
    const coding = runtime?.kind === "agent" ? runtime : undefined;
    const codingDetail = useRuntime(coding?.id ?? null);
    const sets = codingDetail.data?.options;
    const codingOptions = coding && sets && pinned ? optionsFor(sets, chosenModel(coding, { runtimeId: coding.id, model: pinned.model })) : [];
    const before = useMemo(() => draftOfAgent(detail), [detail]);
    const [draft, setDraft] = useState<AgentDraft>(before);
    const found = agentProblems(draft);
    const dirty = hasAgentChanges(before, draft);
    const blank: Omit<FormField, "name" | "label" | "kind"> = { description: null, required: false, options: [], readOnly: false, reference: null, integer: false };

    const generation: FormField[] = pinned && !coding && !runtimes.isLoading ? [
        ...(model?.reasons || draft.reasoningEffort ? [{ ...blank, name: "effort", label: "Reasoning effort", kind: "select" as const, options: [{ value: "", label: "Model default" }, ...REASONING_EFFORTS.map((one) => ({ value: one, label: one[0].toUpperCase() + one.slice(1) }))] }] : []),
        { ...blank, name: "max", label: "Max output tokens", kind: "text", description: "A whole number, or empty for the model’s own limit." },
    ] : [];
    const options: FormField[] = pinned && coding ? codingOptions.map((option) => ({
        ...blank, name: "option:" + option.key, label: option.label, kind: "select" as const, description: option.description || null,
        options: [{ value: "", label: "Agent’s default" }, ...option.choices.map((one) => ({ value: one.value, label: one.label }))],
    })) : [];

    return (
        <View style={{ paddingTop: 12, gap: 18, paddingHorizontal: 4 }}>
            <Form
                fields={[{ ...blank, name: "description", label: "Description", kind: "text", description: "One line, shown wherever this agent is listed. Clearing it is allowed." }]}
                values={{ description: draft.description }}
                onChange={(_, value) => setDraft({ ...draft, description: String(value ?? "") })}
            />
            <View style={{ gap: 6 }}>
                <Text size={13.5} tone={color.ink2}>Instruction<Text size={13.5} tone={color.ink3}> · needed</Text></Text>
                <TextInput
                    accessibilityLabel="Instruction"
                    multiline
                    value={draft.instruction}
                    editable={!writes.save.isPending}
                    onChangeText={(instruction) => setDraft({ ...draft, instruction })}
                    style={{ minHeight: 260, maxHeight: 480, borderWidth: 1, borderColor: color.line2, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, textAlignVertical: "top", fontFamily: font.regular, fontSize: 15, lineHeight: 21, color: color.ink }}
                />
                <Text size={13} tone={color.ink3}>Instructions it follows each time it runs.</Text>
                {found.instruction ? <Text size={13} tone={color.amber}>{found.instruction}</Text> : null}
            </View>
            {pinned && coding && codingDetail.isError ? <Note style={{ paddingHorizontal: 0 }}>Couldn’t read {coding.name}’s options from its computer, so its effort stays as that computer has it.</Note> : null}
            {options.length > 0 ? (
                <Form
                    fields={options}
                    values={Object.fromEntries(codingOptions.map((option) => ["option:" + option.key, draft.selections[option.key] ?? ""]))}
                    onChange={(field, value) => setDraft({ ...draft, selections: { ...draft.selections, [field.slice(7)]: String(value ?? "") } })}
                />
            ) : null}
            {generation.length > 0 ? (
                <Form
                    fields={generation}
                    values={{ effort: draft.reasoningEffort, max: draft.maxOutputTokens }}
                    errors={found.maxOutputTokens ? { max: found.maxOutputTokens } : {}}
                    onChange={(field, value) => setDraft(field === "effort" ? { ...draft, reasoningEffort: String(value ?? "") } : { ...draft, maxOutputTokens: String(value ?? "") })}
                />
            ) : null}
            <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>Toolsets, visibility and the model are set by asking {detail.label} for them, or in settings.</Text>
            {writes.save.isError ? <Problem text={writes.save.error instanceof Error ? writes.save.error.message : "That was not saved."} onDismiss={() => writes.save.reset()} /> : null}
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                <Button title={writes.save.isPending ? "Saving…" : "Save"} busy={writes.save.isPending} disabled={!dirty || Object.keys(found).length > 0} onPress={() => writes.save.mutate({ before, after: draft }, { onSuccess: onDone })} />
                <Button title="Cancel" kind="outline" disabled={writes.save.isPending} onPress={onDone} />
                {dirty && !writes.save.isPending ? (
                    <Press label="Put it back" onPress={() => setDraft(before)} style={{ minHeight: 40, justifyContent: "center", paddingHorizontal: 6 }}>
                        <Text size={14} tone={color.ink2}>Put it back</Text>
                    </Press>
                ) : null}
            </View>
        </View>
    );
}
