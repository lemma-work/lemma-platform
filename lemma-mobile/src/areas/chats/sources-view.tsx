import { Share, View } from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import { copyText } from "../../thread/clipboard";
import { Loading } from "../../ui/chrome";
import { Button } from "../../ui/press";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { Label, SwitchRow, TextAction, Trouble } from "./bits";
import { chatKeys, useChatsSource, useMyId, useSurfaces } from "./hooks";
import { isForbidden } from "./model";
import { problemOf } from "@web/channels/problem";
import {
    answering,
    howToAsk,
    howToRemove,
    inviteText,
    linkShown,
    offersTakeOn,
    onlyWho,
    platformName,
    refusedChange,
    sayAnswering,
    takeOn,
    toldWhenChanged,
    whomItAnswers,
    type SourceChange,
    type SourceSettings,
} from "@web/channels/sources";
import type { PublicReach } from "@web/data/types";

/** One group a chat is fed from, from the Lemma's side (the web's
 *  `SourceSettingsView`): how its people join, who answers its people from
 *  outside the Lemma, from what, and how they ask -- with the switch and
 *  "Take it on" for a reader the server lets change it. Anyone else is told
 *  who can. */
export function SourceSettingsView({ podId, channelId, sourceId, lemmaName }: { podId: string; channelId: string; sourceId: string; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const settings = useQuery({ queryKey: chatKeys.source(key, podId, sourceId), queryFn: () => source.source(podId, channelId, sourceId), staleTime: 30_000 });
    if (settings.isPending) return <Loading label="Reading its settings" />;
    if (!settings.data) return <Trouble>{problemOf(settings.error, "Couldn’t read its settings.")}</Trouble>;
    return <Settings podId={podId} settings={settings.data} lemmaName={lemmaName} />;
}

function Settings({ podId, settings, lemmaName }: { podId: string; settings: SourceSettings; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMyId();
    const surfaces = useSurfaces(podId);
    const surface = (surfaces.data ?? []).find((one) => one.name === settings.surfaceName) ?? null;
    /* Who answers here: the Lemma, unless the group is one of its other agents'. */
    const bot = surface && !surface.mine ? surface.agentName || lemmaName : lemmaName;
    const change = useMutation({
        mutationFn: (body: SourceChange) => source.updateSource(podId, settings.channelId, settings.id, body),
        onSuccess: (saved) => cache.setQueryData(chatKeys.source(key, podId, settings.id), saved),
        onError: () => void cache.invalidateQueries({ queryKey: chatKeys.source(key, podId, settings.id) }),
    });
    const asked = change.isPending ? change.variables : null;
    const on = asked?.answers_outsiders ?? settings.answersOutsiders;
    const state = answering(settings, me);
    const may = settings.canManage;
    const told = may ? toldWhenChanged(state) : null;
    const who = may ? null : onlyWho(state, lemmaName, bot);
    const remove = howToRemove(settings.platform, settings.platform === "SLACK" ? surface?.handle || bot : bot);
    const link = settings.inviteLink;

    return (
        <View style={{ gap: 6 }}>
            {settings.pending ? <Text size={14} tone={color.ink2}>WhatsApp is still making this group. Its link appears here in a few seconds.</Text> : null}
            {link ? (
                <View style={{ gap: 6 }}>
                    <Label>People join by</Label>
                    <Text size={14.5} numberOfLines={1} style={{ paddingHorizontal: 4 }}>{linkShown(link)}</Text>
                    <View style={{ flexDirection: "row", gap: 8 }}>
                        <Button title="Copy the link" kind="outline" height={36} size={14} onPress={() => copyText(link)} />
                        <Button title="Send it" kind="outline" height={36} size={14} onPress={() => void Share.share({ message: inviteText(settings.title ?? "the group", link) })} />
                    </View>
                </View>
            ) : null}
            <Label>{"People outside " + lemmaName}</Label>
            {state.kind === "invited" ? (
                <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>People here who are not in {lemmaName} get a private note inviting them in. Nobody is answered from what is Public in this Slack channel.</Text>
            ) : (
                <>
                    {may ? (
                        <SwitchRow label="Answer them" note={"From what is Public in " + lemmaName + ", listed below."} value={on} disabled={change.isPending || settings.pending} onChange={(next) => change.mutate({ answers_outsiders: next })} />
                    ) : null}
                    {state.kind === "bot-off" ? (
                        <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>Answering people outside {lemmaName} is off for this {platformName(state.platform)} number. Change it in {lemmaName}’s settings.</Text>
                    ) : !(may && (state.kind === "off" || state.kind === "pending")) ? (
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                            <Text size={14.5}>{sayAnswering(state, lemmaName)}</Text>
                            {offersTakeOn(state, may) ? <TextAction label={change.isPending && change.variables?.owner === "me" ? "Taking it on…" : "Take it on"} tone={color.ink} disabled={change.isPending} onPress={() => change.mutate(takeOn(settings))} /> : null}
                        </View>
                    ) : null}
                    {told ?? who ? <Text size={13} tone={color.ink3}>{told ?? who}</Text> : null}
                    {settings.publicReach ? <PublicList reach={settings.publicReach} lemmaName={lemmaName} /> : null}
                    {change.isError ? <Trouble>{isForbidden(change.error) ? refusedChange(lemmaName) : "Couldn’t change that. Try again."}</Trouble> : null}
                </>
            )}
            <Text size={13} tone={color.ink3} style={{ lineHeight: 18, paddingTop: 4 }}>{howToAsk(settings.platform, bot)} {whomItAnswers(settings, lemmaName)}</Text>
            {remove ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{remove}</Text> : null}
        </View>
    );
}

/** What people from outside can be answered from (the web's `SourcePublic`). */
function PublicList({ reach, lemmaName }: { reach: PublicReach; lemmaName: string }) {
    const empty = reach.files.length === 0 && reach.tables.length === 0 && !reach.more;
    return (
        <View style={{ gap: 4, paddingTop: 4 }}>
            <Label>What they can be told</Label>
            {empty ? <Text size={14} tone={color.ink2}>Nothing in {lemmaName} is Public, so they are answered only from what is said here.</Text> : null}
            {reach.files.map((file) => (
                <View key={file.path ?? file.name} style={{ flexDirection: "row", gap: 8, minHeight: 32, alignItems: "center", paddingHorizontal: 4 }}>
                    <Text size={14.5} numberOfLines={1} style={{ flex: 1 }}>{file.name}</Text>
                    <Text size={12.5} tone={color.ink3} numberOfLines={1}>{file.path === null ? "Someone’s own files" : file.path.slice(0, file.path.lastIndexOf("/")) || "Top of " + lemmaName}</Text>
                </View>
            ))}
            {reach.tables.map((table) => (
                <View key={"t:" + table} style={{ flexDirection: "row", gap: 8, minHeight: 32, alignItems: "center", paddingHorizontal: 4 }}>
                    <Text size={14.5} numberOfLines={1} style={{ flex: 1 }}>{table}</Text>
                    <Text size={12.5} tone={color.ink3}>Table</Text>
                </View>
            ))}
            {reach.more ? <Text size={13} tone={color.ink3} style={{ paddingHorizontal: 4 }}>and more marked Public</Text> : null}
            <Text size={12.5} tone={color.ink3} style={{ paddingHorizontal: 4 }}>A file shared with anyone with a Lemma account is Public too.</Text>
        </View>
    );
}
