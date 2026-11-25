import { useMemo, useState } from "react";
import { ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import type { Org } from "../../../data/types";
import { IconButton } from "../../../ui/chrome";
import { Button } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Counted, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useMyOrgRole } from "../access/hooks";
import { settingsEntry } from "../sections";
import { ChoiceRow, Group, ReadProblem, Reading, Row, RowIcon, Say, isForbidden, problemOf, SettingsScreen } from "../ui";
import { canManage } from "@web/org/membership";
import {
    useArchive, useComputers, useInherited, useOrgDefault, useRefreshModels, useRestore, useRuntimes, useSetOrgDefault, useTestKey,
} from "./hooks";
import { agentRow, agentSetTo, computerWords, countWords, defaultChoices, keyRow, kindOf, ledger, testWords, testable, whoPicks } from "./model";
import { StateDot, WithOrg } from "./parts";
import { modelsRoutes } from "./routes";
import { canBeOrganizationDefault } from "@web/org/provider-draft";
import { agentFix, describeChoice, shortModel, stillLooking, type Computer, type LocalAgent, type Runtime } from "@web/data/runtimes";

export { LemmaModelScreen } from "./lemma-model";
export { AddKeyScreen } from "./add-key";

/** Everything a Lemma in this organization can run on (the web's
 *  `org/models.tsx`): the organization's default, its provider keys, and the
 *  coding agents on the computers paired to this account. Keys are added,
 *  tested, retired and brought back here; coding agents are only read, since
 *  they are set up on their own computer. */
export function ModelsScreen() {
    const entry = settingsEntry("models");
    const title = entry?.title ?? "Models";
    const note = entry?.hint ?? null;
    return (
        <WithOrg title={title} note={note} back="Account">
            {(org) => <Models org={org} title={title} note={note} />}
        </WithOrg>
    );
}

/** Which agent's sheet is open: one on a computer, or a saved one whose
 *  computer is not listed here. */
interface OpenAgent {
    agent: LocalAgent | null;
    computer: Computer | null;
    saved: Runtime | null;
}

function Models({ org, title, note }: { org: Org; title: string; note: string | null }) {
    const router = useRouter();
    const runtimes = useRuntimes(org.id);
    const chosen = useOrgDefault(org.id);
    const inherited = useInherited(org.id);
    const computers = useComputers(true);
    const refresh = useRefreshModels(org.id);
    const setDefault = useSetOrgDefault(org.id);
    const { role } = useMyOrgRole(org.id);
    /* Nothing is withheld on a guess: until the role is known, the server
       refuses what it must. */
    const manage = role === null || canManage(role);
    const [showRetired, setShowRetired] = useState(false);
    const [keyOpen, setKeyOpen] = useState<string | null>(null);
    const [agentOpen, setAgentOpen] = useState<OpenAgent | null>(null);

    const all = useMemo(() => runtimes.data ?? [], [runtimes.data]);
    const machines = useMemo(() => computers.data ?? [], [computers.data]);
    const book = useMemo(() => ledger(all, machines), [all, machines]);
    const live = all.filter((one) => !one.archived);
    const keys = book.keys.filter((one) => showRetired || !one.archived);
    const elsewhere = book.elsewhere.filter((one) => showRetired || !one.archived);
    const choices = defaultChoices(all);
    const current = chosen.data ?? null;
    const inheritedName = describeChoice(live, inherited.data ?? null);
    const opened = keyOpen ? all.find((one) => one.id === keyOpen) ?? null : null;

    return (
        <SettingsScreen title={title} note={note} back="Account" right={<IconButton name="refresh" label="Read the models again" onPress={refresh} />}>
            {runtimes.isSuccess ? (
                <Counted size={14} tone={color.ink2}>{countWords(book.available, book.troubled)}</Counted>
            ) : null}

            {runtimes.isSuccess && book.available === 0 ? (
                <View style={{ gap: 6 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <View accessibilityLabel="Needs you" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} />
                        <Text size={16} weight="medium">Add a model to get answers</Text>
                    </View>
                    <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>
                        Until one is added here, every message comes back unanswered. Connect a provider's API key, or run a model yourself with Ollama or LM Studio on the computer Lemma runs on.
                    </Text>
                    {!manage ? <Say>Ask an owner or editor of {org.name} to add one.</Say> : null}
                </View>
            ) : null}

            {runtimes.isSuccess ? (
                <Group label="Default model" foot={setDefault.isError ? undefined : current ? "Lemmas run on it unless they name their own." : inheritedName ? `Until one is chosen, Lemmas run on ${inheritedName}.` : undefined}>
                    {manage && choices.length > 0 ? (
                        <ChoiceRow
                            label="Everyone's default"
                            hint={setDefault.isPending ? "Saving…" : null}
                            title="Everyone's default model"
                            choices={choices}
                            value={current?.runtimeId ?? null}
                            onPick={(runtimeId) => { if (runtimeId !== current?.runtimeId) setDefault.mutate({ runtimeId, model: "" }); }}
                            last
                        />
                    ) : (
                        <Row
                            label="Everyone's default"
                            value={current ? describeChoice(live, current) || "Not available" : "Not chosen"}
                            hint={manage && choices.length === 0 ? "Connect a key to choose one" : null}
                            last
                        />
                    )}
                    {setDefault.isError ? (
                        <Say tone="bad">{isForbidden(setDefault.error) ? `Only owners and editors of ${org.name} choose its default.` : problemOf(setDefault.error, "That could not be made the default. Try again.")}</Say>
                    ) : null}
                </Group>
            ) : null}

            <Group label="Provider keys">
                {runtimes.isPending ? <Reading /> : runtimes.isError ? (
                    <ReadProblem said={isForbidden(runtimes.error) ? `You may not see ${org.name}'s models.` : problemOf(runtimes.error, "The models could not be read.")} onRetry={refresh} />
                ) : keys.length === 0 ? (
                    <Say>No keys yet.</Say>
                ) : keys.map((runtime, index) => {
                    const row = keyRow(runtime, current?.runtimeId === runtime.id);
                    const last = index === keys.length - 1 && book.retired === 0 && !manage;
                    return (
                        <Row
                            key={runtime.id}
                            glyph={<StateDot tone={row.tone} />}
                            label={runtime.name}
                            hint={row.hint}
                            value={row.value}
                            onPress={runtime.scope === "system" ? undefined : () => setKeyOpen(runtime.id)}
                            last={last}
                        />
                    );
                })}
                {runtimes.isSuccess && book.retired > 0 ? (
                    <Row label={showRetired ? "Hide retired" : "Show retired"} role="button" onPress={() => setShowRetired((was) => !was)} end={<View />} last={!manage} />
                ) : null}
                {manage ? <Row label="Connect a key" glyph={<RowIcon name="plus" />} hint="Billed to the provider account, shared with everyone here" onPress={() => router.push(modelsRoutes.addKey as never)} last /> : null}
            </Group>

            <Group label="Coding agents" foot="Each is set up on its own computer, in the Lemma app there. Here you can see what they are set to.">
                {computers.isPending ? <Reading /> : computers.isError ? (
                    <ReadProblem said={problemOf(computers.error, "Your computers could not be read.")} onRetry={() => void computers.refetch()} />
                ) : machines.length === 0 ? (
                    <Say>Claude Code and Codex already live on your computer. Install the Lemma app there and sign in: it connects itself, and the coding agents it finds show up here.</Say>
                ) : null}
            </Group>

            {machines.map((computer) => (
                <Group
                    key={computer.id}
                    label={computer.name}
                    right={<Text size={12.5} tone={color.ink3}>{computer.status}</Text>}
                    foot={computerWords(computer) || undefined}
                >
                    {stillLooking(computer) ? <Reading label="Finding coding agents" /> : computer.agents.length === 0 ? (
                        <Say>
                            {computer.online
                                ? "No coding agents found. Install Claude Code, Codex, Cursor or OpenCode on that computer and it shows up here."
                                : "Nothing published. It reports what it finds when it is next awake."}
                        </Say>
                    ) : computer.agents.map((agent, index) => {
                        const saved = book.savedByAgent.get(agent.id) ?? null;
                        const row = agentRow(agent, computer, saved);
                        return (
                            <Row
                                key={agent.id}
                                glyph={<StateDot tone={row.tone} />}
                                label={row.name}
                                hint={row.hint}
                                value={row.value}
                                onPress={() => setAgentOpen({ agent, computer, saved })}
                                last={index === computer.agents.length - 1}
                            />
                        );
                    })}
                </Group>
            ))}

            {elsewhere.length > 0 ? (
                <Group label="On other computers">
                    {elsewhere.map((runtime, index) => {
                        const row = keyRow(runtime, false);
                        return (
                            <Row
                                key={runtime.id}
                                glyph={<StateDot tone={row.tone} />}
                                label={runtime.name}
                                hint={row.hint}
                                value={row.value}
                                onPress={() => setAgentOpen({ agent: null, computer: null, saved: runtime })}
                                last={index === elsewhere.length - 1}
                            />
                        );
                    })}
                </Group>
            ) : null}

            {book.troubled > 0 ? (
                <Say>Anything set to run on something unavailable stops answering until that computer is back, or until it is pointed somewhere else.</Say>
            ) : null}

            {opened ? (
                <KeySheet org={org} runtime={opened} isDefault={current?.runtimeId === opened.id} manage={manage} onClose={() => setKeyOpen(null)} />
            ) : null}
            {agentOpen ? <AgentSheet org={org} open={agentOpen} onClose={() => setAgentOpen(null)} /> : null}
        </SettingsScreen>
    );
}

function hostOf(url: string | undefined): string {
    if (!url) return "";
    try {
        return new URL(url).host;
    } catch {
        return url;
    }
}

/** One key: its state and what it runs, and, for whoever may, a test, making
 *  it everyone's default, and retiring it or bringing it back. Retiring asks
 *  first, in the same sheet. */
function KeySheet({ org, runtime, isDefault, manage, onClose }: { org: Org; runtime: Runtime; isDefault: boolean; manage: boolean; onClose: () => void }) {
    const test = useTestKey(org.id);
    const setDefault = useSetOrgDefault(org.id);
    const archive = useArchive(org.id);
    const restore = useRestore(org.id);
    const [asking, setAsking] = useState(false);
    const row = keyRow(runtime, isDefault);
    const may = manage || runtime.scope === "personal";
    const own = runtime.defaultModel ? runtime.models.find((one) => one.name === runtime.defaultModel)?.label ?? shortModel(runtime.defaultModel) : "";
    const refused = (error: unknown, fallback: string) => (isForbidden(error) ? `Only owners and editors of ${org.name} can change its keys.` : problemOf(error, fallback));

    return (
        <Sheet open onClose={() => { if (!archive.isPending) onClose(); }} title={runtime.name} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 14 }}>
                {asking ? (
                    <View style={{ gap: 14 }}>
                        <Text size={18} weight="medium">Retire {runtime.name}?</Text>
                        <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>
                            It leaves every list a model is picked from, and any Lemma set to it will need another. {isDefault ? "It stops being everyone's default. " : ""}You can bring it back.
                        </Text>
                        {archive.isError ? <Say tone="bad">{refused(archive.error, `${runtime.name} was not retired. Try again.`)}</Say> : null}
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Keep" kind="outline" grow disabled={archive.isPending} onPress={() => { archive.reset(); setAsking(false); }} />
                            <Button title={archive.isPending ? "Retiring…" : "Retire"} grow busy={archive.isPending} onPress={() => archive.mutate(runtime.id, { onSuccess: onClose })} />
                        </View>
                    </View>
                ) : (
                    <>
                        <Group>
                            <Row label="State" glyph={<StateDot tone={row.tone} />} value={row.value} />
                            <Row label="Kind" value={kindOf(runtime)} />
                            {runtime.baseUrl ? <Row label="Address" value={hostOf(runtime.baseUrl)} /> : null}
                            <Row label="Runs unless told otherwise" value={own || "The provider's own"} />
                            <Row label="Models" value={String(runtime.models.length)} last />
                        </Group>
                        {test.isPending ? <Say>Asking {runtime.name} to answer…</Say>
                            : test.data ? <Say tone={test.data.ok ? "done" : "bad"}>{testWords(test.data)}</Say>
                                : test.isError ? <Say tone="bad">{refused(test.error, "The test could not be run. Try again.")}</Say> : null}
                        {setDefault.isError ? <Say tone="bad">{refused(setDefault.error, "That could not be made the default. Try again.")}</Say> : null}
                        {restore.isError ? <Say tone="bad">{refused(restore.error, `${runtime.name} was not brought back. Try again.`)}</Say> : null}
                        {!may ? (
                            <Say>Only owners and editors of {org.name} can change its keys.</Say>
                        ) : runtime.archived ? (
                            <View style={{ flexDirection: "row" }}>
                                <Button title={restore.isPending ? "Bringing back…" : "Bring back"} kind="outline" busy={restore.isPending} onPress={() => restore.mutate(runtime.id, { onSuccess: onClose })} />
                            </View>
                        ) : (
                            <View style={{ gap: 10 }}>
                                <View style={{ flexDirection: "row", gap: 10 }}>
                                    {testable(runtime) ? (
                                        <Button title={test.isPending ? "Testing…" : "Test"} kind="outline" grow busy={test.isPending} onPress={() => test.mutate(runtime.id)} />
                                    ) : null}
                                    {canBeOrganizationDefault(runtime) && !isDefault ? (
                                        <Button title={setDefault.isPending ? "Saving…" : "Make default"} kind="outline" grow busy={setDefault.isPending} onPress={() => setDefault.mutate({ runtimeId: runtime.id, model: "" })} />
                                    ) : null}
                                </View>
                                <View style={{ flexDirection: "row" }}>
                                    <Button title="Retire" kind="quiet" onPress={() => setAsking(true)} />
                                </View>
                            </View>
                        )}
                    </>
                )}
            </ScrollView>
        </Sheet>
    );
}

/** One coding agent, read only: where it is, whether it can take work, and
 *  what it is set to. Said plainly that it is set up on its own computer. */
function AgentSheet({ org, open, onClose }: { org: Org; open: OpenAgent; onClose: () => void }) {
    const { agent, computer, saved } = open;
    const row = agent && computer ? agentRow(agent, computer, saved) : saved ? { ...keyRow(saved, false), name: saved.name } : null;
    const name = row?.name ?? "A coding agent";
    const fix = agent && computer?.online && !agent.ready ? agentFix(agent, null) : "";
    const there = computer ? computer.name : "its computer";
    return (
        <Sheet open onClose={onClose} title={name} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 14 }}>
                <Group>
                    {row ? <Row label="State" glyph={<StateDot tone={row.tone} />} value={row.value} /> : null}
                    <Row label="Computer" value={computer?.name ?? "Not one of yours"} />
                    {agent?.version ? <Row label="Version" value={agent.version} /> : null}
                    <Row label="Who can pick it" value={saved && !saved.archived ? whoPicks(saved, org.name) : "Nobody yet"} last />
                </Group>
                {fix ? <Say tone="bad">{fix}</Say> : null}
                {saved && !saved.archived ? (
                    <Group label="Set to">
                        {agentSetTo(agent, saved).map((one, index, rows) => (
                            <Row key={one.label} label={one.label} value={one.value} last={index === rows.length - 1} />
                        ))}
                    </Group>
                ) : saved ? (
                    <Say>Retired, so no Lemma can run on it. It can be brought back under Models, in the Lemma app or on the web.</Say>
                ) : (
                    <Say>Not added yet, so no Lemma can run on it. It is added under Models, in the Lemma app or on the web.</Say>
                )}
                <Say>Coding agents are set up on their own computer. To change what this one is set to, use the Lemma app on {there}.</Say>
            </ScrollView>
        </Sheet>
    );
}
