import { useState } from "react";
import { Linking, View } from "react-native";
import { useRouter } from "expo-router";
import type { AgentSurfaceResponse } from "lemma-sdk";
import { useLemma, useOrg } from "../../../data/hooks";
import { Loading } from "../../../ui/chrome";
import { Button, Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { KnownSender } from "../account/verify-mobile";
import { lemmaPart } from "../sections";
import { ChoiceRow, ChoiceSheet, Confirm, Copyable, Field, Group, ReadProblem, Reading, Row, Say, SettingsScreen, ToggleRow, isForbidden, problemOf } from "../ui";
import { GroupsHere } from "./groups";
import { useAnswerers, useDisconnect, usePlaceDetail, usePlaceOptions, usePlaceSetup, usePlaces, useSavePlace } from "./hooks";
import { addressLabel, addressOf, answerChoices, answererChoices, answererName, hasGroups, pauses, placeKey, placeName, routesHeading, sayHi, sayHiLabel } from "./places";
import { Health } from "./setup";
import type { Surface } from "./types";
import { contactsSupported, filtersSupported, isOwnBot, routesSupported, surfaceDraft, surfacePatch, type SurfaceDraft } from "@web/data/surface-settings";

const PART = lemmaPart("channels");

/** One place a Lemma answers, in plain words (the web's `surface-manage.tsx`):
 *  its address to copy and the way to say hi, whether it works and the steps
 *  left, whether the person who connected it will be known on WhatsApp, then
 *  its settings, each said about the name that answers: on or paused, who
 *  answers, where in Slack, whose mail, whether it may write first, people
 *  outside, contacts. Then its groups, and the way to disconnect it. */
export function PlaceScreen({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const lemma = useLemma(podId);
    const space = lemma?.name ?? "This Lemma";
    const places = usePlaces(podId);
    const listed = places.data?.find((one) => one.name === name) ?? null;
    const detail = usePlaceDetail(podId, name);
    const setup = usePlaceSetup(podId, name);
    const back = PART?.label ?? "Back";

    if (places.isPending) return <SettingsScreen title="" back={back}><Loading /></SettingsScreen>;
    if (!listed) {
        return (
            <SettingsScreen title="Not connected" back={back}>
                {places.isError
                    ? <ReadProblem said={problemOf(places.error, "This place could not be read.")} onRetry={() => void places.refetch()} />
                    : <Say>{`${space} does not answer there any more.`}</Say>}
            </SettingsScreen>
        );
    }

    const platform = listed.platform;
    const title = placeName(platform);
    /* Who answers here: the Lemma, or the agent this place belongs to. */
    const bot = listed.mine ? space : listed.agentName || space;
    const address = addressOf(listed);
    const hi = sayHi(listed);

    return (
        <SettingsScreen title={title} note={listed.mine ? null : `Answers as ${bot}.`} back={back}>
            {address ? (
                <Group label={addressLabel(platform)}>
                    <Copyable value={address} label={addressLabel(platform).replace(/^Its /, "its ")} mono={placeKey(platform) === "WHATSAPP"} />
                    {hi && listed.active ? (
                        <Press role="link" label={sayHiLabel(platform)} onPress={() => void Linking.openURL(hi).catch(() => undefined)} style={{ alignSelf: "flex-start", minHeight: 44, justifyContent: "center" }}>
                            <Text size={15} weight="medium">{sayHiLabel(platform)}</Text>
                        </Press>
                    ) : null}
                </Group>
            ) : null}

            <Health
                setup={setup.data}
                pending={setup.isPending}
                failed={setup.isError}
                checking={setup.isFetching || detail.isFetching}
                onCheck={() => { void setup.refetch(); void detail.refetch(); }}
            />

            {listed.mine && placeKey(platform) === "WHATSAPP" ? <KnownSender /> : null}

            {detail.isPending ? <Reading label="Reading its settings" /> : detail.isError ? (
                <ReadProblem
                    said={isForbidden(detail.error) ? `You may not see how ${bot} answers here.` : problemOf(detail.error, "Its settings could not be read.")}
                    onRetry={() => void detail.refetch()}
                />
            ) : (
                <PlaceForm key={detail.data.id} podId={podId} space={space} listed={listed} detail={detail.data} bot={bot} onDone={() => router.back()} />
            )}
        </SettingsScreen>
    );
}

function PlaceForm({ podId, space, listed, detail, bot, onDone }: {
    podId: string;
    space: string;
    listed: Surface;
    detail: AgentSurfaceResponse;
    bot: string;
    onDone: () => void;
}) {
    const { org } = useOrg();
    const [draft, setDraft] = useState<SurfaceDraft>(() => surfaceDraft(detail));
    const [start] = useState<SurfaceDraft>(() => surfaceDraft(detail));
    const [dropping, setDropping] = useState(false);
    const [choosing, setChoosing] = useState(false);
    const platform = String(detail.platform);
    const agents = useAnswerers(podId);
    const options = usePlaceOptions(podId, detail.name, routesSupported(platform));
    const save = useSavePlace(podId, detail.name);
    const drop = useDisconnect(podId, org?.id);
    const change = (patch: Partial<SurfaceDraft>) => setDraft((current) => ({ ...current, ...patch }));
    const name = placeName(platform);
    const answerer = answererName(draft.agent, space, agents.data, bot);
    const groups = hasGroups(platform);
    const own = isOwnBot(detail);
    const contacts = answerChoices(space);
    const dirty = JSON.stringify(surfacePatch(platform, draft)) !== JSON.stringify(surfacePatch(platform, start));
    const choices = options.data ?? [];
    const free = choices.filter((option) => !draft.channels.some((route) => route.channel_id === option.id));
    const working = save.isPending || drop.isPending;

    return (
        <>
            <Group label="How it answers">
                <ToggleRow label={`${answerer} answers on ${name}`} hint={pauses(platform)} value={draft.enabled} onChange={(enabled) => change({ enabled })} disabled={working} />
                <ChoiceRow
                    label="Who answers"
                    title="Who answers"
                    hint={`The one that answers every chat${groups ? " and group" : ""} here.`}
                    choices={answererChoices(space, agents.data ?? [], draft.agent)}
                    value={draft.agent}
                    onPick={(agent) => change({ agent })}
                    disabled={working}
                />
                <ToggleRow label={`${answerer} can write first`} hint="In chats that already exist, for a reminder or a follow-up. Off: it only replies." value={draft.allowSend} onChange={(allowSend) => change({ allowSend })} disabled={working} last={!groups && !contactsSupported(platform)} />
                {groups ? (
                    <ToggleRow label={`Answer people outside ${space}`} hint={`From what ${space} has made Public, in every group it is in.`} value={draft.answersOutsiders} onChange={(answersOutsiders) => change({ answersOutsiders })} disabled={working} last={!contactsSupported(platform)} />
                ) : null}
                {contactsSupported(platform) ? (
                    <>
                        <ChoiceRow
                            label="Who it answers privately"
                            title={`Private messages from people outside ${space}`}
                            choices={contacts}
                            value={draft.contactsAnswer}
                            onPick={(contactsAnswer) => change({ contactsAnswer })}
                            disabled={!own || working}
                            last
                        />
                        <Text size={13} tone={color.ink3} style={{ lineHeight: 18, paddingTop: 2 }}>
                            {own
                                ? contacts.find((choice) => choice.value === draft.contactsAnswer)?.hint ?? ""
                                : `People outside ${space} are answered privately only on a number, address or Telegram bot of its own, not on Lemma's shared one.`}
                        </Text>
                    </>
                ) : null}
                {agents.isError ? (
                    <Press label="Read who can answer again" onPress={() => void agents.refetch()} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
                        <Text size={14.5} weight="medium">Read who can answer again</Text>
                    </Press>
                ) : null}
            </Group>

            {routesSupported(platform) ? (
                <Group label={routesHeading(platform)} foot={`Invite ${answerer} in ${name} first, or nothing there reaches it.`}>
                    {options.isPending ? <Reading label={`Reading ${name}`} /> : null}
                    {options.isError ? <ReadProblem said={problemOf(options.error, `${name} could not be read.`)} onRetry={() => void options.refetch()} /> : null}
                    {draft.channels.map((route) => {
                        const option = choices.find((one) => one.id === route.channel_id);
                        return (
                            <Row
                                key={route.channel_id}
                                label={`#${route.channel_name || option?.name || route.channel_id}`}
                                hint={option?.isMember === false ? `Invite ${answerer} there first, or nothing there reaches it.` : null}
                                end={(
                                    <Press label={`Stop answering in ${route.channel_name || route.channel_id}`} disabled={working} onPress={() => change({ channels: draft.channels.filter((one) => one.channel_id !== route.channel_id) })} style={{ minHeight: 44, justifyContent: "center", paddingLeft: 8 }}>
                                        <Text size={14.5} weight="medium">Remove</Text>
                                    </Press>
                                )}
                            />
                        );
                    })}
                    {options.isSuccess && choices.length === 0 ? <Say>{`Nowhere yet. Invite ${answerer} in ${name}, then look again.`}</Say> : null}
                    <View style={{ flexDirection: "row", flexWrap: "wrap", columnGap: 18 }}>
                        <Press label={`Add a place in ${name}`} disabled={!free.length || working} onPress={() => setChoosing(true)} style={{ minHeight: 44, justifyContent: "center", opacity: free.length ? 1 : 0.45 }}>
                            <Text size={15} weight="medium">Add a place in {name}</Text>
                        </Press>
                        <Press label="Look again" onPress={() => void options.refetch()} style={{ minHeight: 44, justifyContent: "center" }}>
                            <Text size={15} weight="medium">Look again</Text>
                        </Press>
                    </View>
                    <ChoiceSheet
                        open={choosing}
                        title={`Where in ${name} ${answerer} answers`}
                        choices={free.map((option) => ({ value: option.id, label: `#${option.name || option.id}`, hint: option.isMember === false ? "Not invited yet" : null }))}
                        value={null}
                        onPick={(id) => change({ channels: [...draft.channels, { channel_id: id, channel_name: choices.find((one) => one.id === id)?.name ?? null }] })}
                        onClose={() => setChoosing(false)}
                    />
                </Group>
            ) : null}

            {filtersSupported(platform) ? (
                <Group label="Whose mail it takes" foot="Separate them with commas, or write one under another. Leave both empty to take mail from anyone the usual rules allow.">
                    <View style={{ gap: 12, paddingTop: 4 }}>
                        <Field label="Only mail from these domains" value={draft.domains} onChange={(domains) => change({ domains })} placeholder="example.com" keyboardType="url" editable={!working} />
                        <Field label="Only mail from these addresses" value={draft.emails} onChange={(emails) => change({ emails })} placeholder="person@example.com" keyboardType="email-address" multiline editable={!working} />
                    </View>
                </Group>
            ) : null}

            {groups ? <GroupsHere podId={podId} space={space} surface={listed} bot={bot} /> : null}

            <View style={{ gap: 10 }}>
                {save.isError ? <Say tone="bad">{isForbidden(save.error) ? `You may not change how ${bot} answers here.` : problemOf(save.error, "That could not be saved. Try again.")}</Say> : null}
                {drop.isError ? <Say tone="bad">{isForbidden(drop.error) ? `You may not disconnect ${name}.` : problemOf(drop.error, "It could not be disconnected. Try again.")}</Say> : null}
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title="Disconnect" kind="outline" grow disabled={working} onPress={() => setDropping(true)} />
                    <Button title={save.isPending ? "Saving…" : "Save"} grow busy={save.isPending} disabled={!dirty || drop.isPending} onPress={() => save.mutate(surfacePatch(platform, draft), { onSuccess: onDone })} />
                </View>
            </View>

            <Confirm
                open={dropping}
                title={`Disconnect ${name}?`}
                body={`${answerer} stops answering here${groups ? ", in every chat and group" : ""}.`}
                confirm={drop.isPending ? "Disconnecting…" : "Disconnect"}
                busy={drop.isPending}
                error={drop.isError ? (isForbidden(drop.error) ? `You may not disconnect ${name}.` : problemOf(drop.error, "It could not be disconnected. Try again.")) : null}
                onClose={() => { if (!drop.isPending) setDropping(false); }}
                onConfirm={() => drop.mutate(detail.name, { onSuccess: () => { setDropping(false); onDone(); } })}
            />
        </>
    );
}
