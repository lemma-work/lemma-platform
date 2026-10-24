import { useState } from "react";
import { ScrollView, View } from "react-native";
import { useLemma, useMe } from "../../../data/hooks";
import { useSession } from "../../../session/session";
import { copyText } from "../../../thread/clipboard";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Counted, Num, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { usePodMembers } from "../people/hooks";
import { agoOf } from "../people/model";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { Copyable, Group, ReadProblem, Reading, Row, RowIcon, Say, SettingsScreen } from "../ui";
import { useDisconnectApp, useGrants, useListen, useMcpEndpoint } from "./hooks";
import { grantLine, plainProblem, whoConnected, type Pod } from "./model";
import {
    MCP_CLIENTS, TOOLS, listeningLine, listeningStatus, reachableFromInternet, serverSteps, setupCommands, setupPrompt, starterPrompts,
    type ConnectedClient,
} from "@web/space/agent-access-model";

/** Using a Lemma from the AI apps somebody already works in (the web's MCP
 *  part of About). The link comes first because it is the easiest way in:
 *  copy it, add it in Claude or ChatGPT, allow Lemma. Each connection acts
 *  as the person who made it and is listed below, to be ended. The Lemma
 *  CLI, the fuller path for a coding agent, is one row further down. */
export function McpScreen({ podId }: PartProps) {
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "this Lemma";
    const pod: Pod = { id: podId, name };
    const part = lemmaPart("mcp")!;
    const endpoint = useMcpEndpoint(podId);
    const [cli, setCli] = useState(false);
    return (
        <SettingsScreen title={part.title(name)} note={part.note(name)} back={lemma?.name ?? "Settings"}>
            {endpoint.isPending ? <Reading /> : null}
            {endpoint.isError ? (
                <Group label="Connect with a link">
                    <ReadProblem said={plainProblem(endpoint.error, "The link could not be loaded.")} onRetry={() => void endpoint.refetch()} />
                </Group>
            ) : null}
            {endpoint.isSuccess && endpoint.data === null ? (
                <Say>This server does not offer MCP, so {name} cannot be added to other AI apps from here.</Say>
            ) : null}
            {endpoint.isSuccess && endpoint.data ? <ByLink pod={pod} url={endpoint.data} /> : null}

            <Group label="Coding agents">
                <Row role="link" glyph={<RowIcon name="app" />} label="Use the Lemma CLI instead" hint="For Codex, Cursor and OpenCode" last onPress={() => setCli(true)} />
            </Group>
            <CliSheet open={cli} pod={pod} onClose={() => setCli(false)} />
        </SettingsScreen>
    );
}

/** The link, how to add it in each app, and the apps already connected. */
function ByLink({ pod, url }: { pod: Pod; url: string }) {
    const [clientId, setClientId] = useState("claude");
    const client = MCP_CLIENTS.find((entry) => entry.id === clientId) ?? MCP_CLIENTS[0];
    const command = client.command?.(url, pod) ?? null;
    const outOfReach = client.remote && !reachableFromInternet(url);
    return (
        <>
            <Group label="Connect with a link">
                <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20, paddingBottom: 4 }}>
                    Paste this link into Claude, ChatGPT or any AI app that speaks MCP, and allow Lemma when it asks. It works in {pod.name} as you, and sees only what you can.
                </Text>
                <Copyable value={url} label="the MCP link" mono />
            </Group>

            <Group label="Add it to">
                <Tabs choices={MCP_CLIENTS.map((entry) => ({ id: entry.id, label: entry.label }))} value={client.id} onPick={setClientId} label="AI app" />
                <View style={{ gap: 10, paddingTop: 12 }}>
                    {outOfReach ? (
                        <Say>{client.label} connects from its own servers, and {pod.name} is only reachable from the computer running Lemma. Use Claude Code, or run Lemma where the internet can reach it.</Say>
                    ) : (
                        <>
                            {command ? (
                                <View style={{ gap: 2 }}>
                                    <Text size={13} tone={color.ink3}>In a terminal:</Text>
                                    <Copyable value={command} label="the command" mono />
                                </View>
                            ) : null}
                            <Steps steps={client.steps(url, pod)} />
                        </>
                    )}
                </View>
            </Group>

            <Connected pod={pod} />
        </>
    );
}

/** Numbered steps, the numbers in Inter. */
function Steps({ steps }: { steps: readonly string[] }) {
    return (
        <View style={{ gap: 8 }}>
            {steps.map((step, index) => (
                <View key={step} style={{ flexDirection: "row", gap: 10 }}>
                    <Num size={14.5} tone={color.ink3} style={{ width: 16, lineHeight: 21 }}>{index + 1}</Num>
                    <Text size={14.5} style={{ flex: 1, lineHeight: 21 }}>{step}</Text>
                </View>
            ))}
        </View>
    );
}

/** A few choices side by side, the one chosen underlined: rules, not boxes. */
function Tabs({ choices, value, onPick, label }: { choices: readonly { id: string; label: string }[]; value: string; onPick: (id: string) => void; label: string }) {
    return (
        <View accessibilityRole="tablist" accessibilityLabel={label} style={{ flexDirection: "row", flexWrap: "wrap", columnGap: 18, borderBottomWidth: 1, borderBottomColor: color.line }}>
            {choices.map((choice) => {
                const on = choice.id === value;
                return (
                    <Press
                        key={choice.id}
                        role="tab"
                        label={choice.label}
                        accessibilityState={{ selected: on }}
                        onPress={() => onPick(choice.id)}
                        style={{ minHeight: TOUCH, justifyContent: "center", borderBottomWidth: 2, borderBottomColor: on ? color.ink : "transparent", marginBottom: -1 }}
                    >
                        <Text size={15} weight={on ? "medium" : "regular"} tone={on ? color.ink : color.ink3}>{choice.label}</Text>
                    </Press>
                );
            })}
        </View>
    );
}

/** The apps connected to this Lemma: everyone's, for its admins, who answer
 *  for what can read it; otherwise the person's own. One opens to what it is
 *  told about and the way to end it. */
function Connected({ pod }: { pod: Pod }) {
    const me = useMe();
    const members = usePodMembers(pod.id);
    const grants = useGrants(pod.id, true);
    const [open, setOpen] = useState<string | null>(null);
    const everyone = grants.data?.everyone ?? false;
    const items = grants.data?.items ?? [];
    const who = (item: ConnectedClient) => whoConnected(item.user_id, me.data?.id ?? null, members.data ?? []);
    const showing = items.find((item) => item.grant_id === open) ?? null;
    return (
        <Group label={everyone ? `Connected to ${pod.name}` : "Connected by you"}>
            {grants.isPending ? <Reading /> : null}
            {grants.isError ? <ReadProblem said={plainProblem(grants.error, "Connected apps could not be loaded.")} onRetry={() => void grants.refetch()} /> : null}
            {grants.isSuccess && items.length === 0 ? <Say>Nothing is connected to {pod.name} yet.</Say> : null}
            {items.map((item, index) => {
                const told = (item.listens_to ?? []).length;
                return (
                    <Row
                        key={item.grant_id}
                        label={item.client_name}
                        hint={grantLine(item, everyone ? who(item) : null) + (told ? ` · told about ${told === 1 ? "one thing" : `${told} things`}` : "")}
                        last={index === items.length - 1}
                        onPress={() => setOpen(item.grant_id)}
                    />
                );
            })}
            <GrantSheet pod={pod} item={showing} who={showing ? who(showing) : ""} mine={showing ? Boolean(me.data?.id) && showing.user_id === me.data?.id : false} everyone={everyone} onClose={() => setOpen(null)} />
        </Group>
    );
}

/** One connected app: where it is from and what it may do, what it is told
 *  about (each stopped or resumed on its own), and ending it. Somebody
 *  else's connection is asked about once before it is ended. */
function GrantSheet({ pod, item, who, mine, everyone, onClose }: {
    pod: Pod;
    item: ConnectedClient | null;
    who: string;
    mine: boolean;
    everyone: boolean;
    onClose: () => void;
}) {
    const disconnect = useDisconnectApp(pod.id);
    const listen = useListen(pod.id);
    const [asking, setAsking] = useState(false);
    const close = () => { setAsking(false); disconnect.reset(); listen.reset(); onClose(); };
    const end = () => {
        if (!item) return;
        disconnect.mutate(item.grant_id, { onSuccess: close });
    };
    const subscriptions = item?.listens_to ?? [];
    return (
        <Sheet open={item !== null} onClose={close} title={item?.client_name} tall>
            {item ? (
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 16 }}>
                    <Counted size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{grantLine(item, everyone ? who : null)}</Counted>
                    {subscriptions.length > 0 ? (
                        <Group label="Told about">
                            {subscriptions.map((subscription, index) => {
                                const stopped = Boolean(subscription.stopped_at);
                                const busy = listen.isPending && listen.variables?.subscriptionId === subscription.id;
                                return (
                                    <Row
                                        key={subscription.id}
                                        label={listeningLine(subscription)}
                                        hint={listeningStatus(subscription, (iso) => agoOf(iso))}
                                        last={index === subscriptions.length - 1}
                                        end={(
                                            <Press
                                                label={`${stopped ? "Resume" : "Stop"} ${listeningLine(subscription)}`}
                                                disabled={listen.isPending}
                                                onPress={() => listen.mutate({ grantId: item.grant_id, subscriptionId: subscription.id, on: stopped })}
                                                style={{ minHeight: TOUCH, justifyContent: "center", paddingHorizontal: 6, opacity: listen.isPending && !busy ? 0.45 : 1 }}
                                            >
                                                <Text size={15} weight="medium">{busy ? (stopped ? "Resuming…" : "Stopping…") : stopped ? "Resume" : "Stop"}</Text>
                                            </Press>
                                        )}
                                    />
                                );
                            })}
                        </Group>
                    ) : null}
                    {listen.isError ? <Say tone="bad">{plainProblem(listen.error, "That did not work. Try again.")}</Say> : null}

                    {asking ? (
                        <View style={{ gap: 10 }}>
                            <Say>End {item.client_name} for {who}? It has to ask again to come back.</Say>
                            <View style={{ flexDirection: "row", gap: 10 }}>
                                <Button title="Keep" kind="outline" grow onPress={() => setAsking(false)} />
                                <Button title={disconnect.isPending ? "Disconnecting…" : "Disconnect"} grow busy={disconnect.isPending} onPress={end} />
                            </View>
                        </View>
                    ) : (
                        <View style={{ gap: 6 }}>
                            <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>Ends the connection and everything it was given. The app has to ask again to come back.</Text>
                            <View style={{ flexDirection: "row" }}>
                                <Button title={disconnect.isPending ? "Disconnecting…" : "Disconnect"} kind="outline" busy={disconnect.isPending} onPress={() => (mine ? end() : setAsking(true))} />
                            </View>
                        </View>
                    )}
                    {disconnect.isError ? <Say tone="bad">{plainProblem(disconnect.error, "It could not be disconnected. Try again.")}</Say> : null}
                </ScrollView>
            ) : null}
        </Sheet>
    );
}

/** The Lemma CLI, for a coding agent: a prompt to paste that sets it up,
 *  the same as commands, and what to ask it once it is. */
function CliSheet({ open, pod, onClose }: { open: boolean; pod: Pod; onClose: () => void }) {
    const { server } = useSession();
    const [toolId, setToolId] = useState("claude");
    const tool = TOOLS.find((entry) => entry.id === toolId) ?? TOOLS[0];
    const servers = serverSteps(server.api, server.web);
    const setup = setupPrompt(pod, tool, servers);
    const commands = setupCommands(pod, tool, servers).join("\n");
    return (
        <Sheet open={open} onClose={onClose} title="The Lemma CLI" tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 18 }}>
                <Tabs choices={TOOLS.map((entry) => ({ id: entry.id, label: entry.label }))} value={tool.id} onPick={setToolId} label="Coding agent" />
                <View style={{ gap: 8 }}>
                    <Text size={15.5} weight="medium">Set up</Text>
                    <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>
                        Paste this into {tool.label}. It installs the Lemma CLI and connects it to {pod.name}; you finish signing in in the browser.
                    </Text>
                    <Text size={13.5} selectable style={{ lineHeight: 19 }}>{setup}</Text>
                    <View style={{ flexDirection: "row" }}><CopyButton text={setup} label="Copy prompt" /></View>
                </View>
                <View style={{ gap: 8 }}>
                    <Text size={15.5} weight="medium">Or run the commands yourself</Text>
                    <Text size={14} tone={color.ink2}>In a terminal, in the folder you work in.</Text>
                    <Num size={13} selectable style={{ lineHeight: 19 }}>{commands}</Num>
                    <View style={{ flexDirection: "row" }}><CopyButton text={commands} label="Copy commands" /></View>
                </View>
                <Group label="Then ask it">
                    {starterPrompts(pod).map((item, index, all) => (
                        <View key={item.title} style={{ gap: 4, paddingVertical: 10, borderBottomWidth: index === all.length - 1 ? 0 : 1, borderBottomColor: color.line }}>
                            <Text size={15.5}>{item.title}</Text>
                            <Text size={13} tone={color.ink3} numberOfLines={3} style={{ lineHeight: 18 }}>{item.prompt}</Text>
                            <View style={{ flexDirection: "row", gap: 18 }}>
                                <CopyButton text={item.prompt} label="Copy" />
                                {tool.launch ? <CopyButton text={tool.launch(item.prompt)} label="Copy command" /> : null}
                            </View>
                        </View>
                    ))}
                </Group>
            </ScrollView>
        </Sheet>
    );
}

/** A word that copies, and says so. */
function CopyButton({ text, label }: { text: string; label: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <Press
            label={label}
            onPress={() => { copyText(text); setCopied(true); setTimeout(() => setCopied(false), 1600); }}
            style={{ minHeight: TOUCH, justifyContent: "center" }}
        >
            <Text size={14.5} weight="medium">{copied ? "Copied" : label}</Text>
        </Press>
    );
}
