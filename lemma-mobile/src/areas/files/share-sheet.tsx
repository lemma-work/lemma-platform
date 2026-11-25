import { useState } from "react";
import { ActivityIndicator, ScrollView, Share, View } from "react-native";
import * as WebBrowser from "expo-web-browser";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Num, Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { FilesIcon } from "./glyphs";
import { useFilesKey, useFilesSource } from "./hooks";
import { expiresIn } from "./model";
import type { SharedLink } from "./source";

/** Sharing a file (the web's `share-dialog.tsx` and the links half of
 *  `share-sheet.tsx`). It asks who, not how: people already in the Lemma get
 *  the permanent link, which needs them signed in; anyone else gets a public
 *  link with bounds, and the bounds are said first, because they are why it
 *  is safe to hand out. What is shown is what the server came back with,
 *  never what was asked for: it clamps both. */

const LIVES = [
    { label: "1 hour", seconds: 3600 },
    { label: "3 hours", seconds: 10800 },
    { label: "24 hours", seconds: 86400 },
];

const OPENS = [10, 50, 100];

function hoursLeft(iso: string): string {
    const left = Date.parse(iso) - Date.now();
    if (!Number.isFinite(left)) return "";
    const hours = Math.max(0, Math.round(left / 3_600_000));
    return hours < 1 ? "under an hour" : hours === 1 ? "an hour" : hours + " hours";
}

function send(url: string) {
    void Share.share({ message: url, url });
}

function Choice({ label, chosen, onPress }: { label: string; chosen: boolean; onPress: () => void }) {
    return (
        <Press role="radio" label={label} accessibilityState={{ selected: chosen }} onPress={onPress} style={{ height: 36, paddingHorizontal: 14, borderRadius: 999, borderWidth: 1, borderColor: chosen ? color.ink : color.line2, backgroundColor: chosen ? color.ink : "transparent", justifyContent: "center" }}>
            <Text size={14} tone={chosen ? color.onInk : color.ink}>{label}</Text>
        </Press>
    );
}

function LiveLink({ link, onRevoke, revoking }: { link: SharedLink; onRevoke: () => void; revoking: boolean }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: TOUCH + 8, borderTopWidth: 1, borderTopColor: color.line }}>
            <View style={{ flex: 1, minWidth: 0 }}>
                <Text size={14.5} numberOfLines={1}>{link.readUrl.replace(/^https?:\/\//, "")}</Text>
                <Text size={12.5} tone={color.ink3}>{expiresIn(link.expiresAt, Date.now())} · up to <Num size={12.5} tone={color.ink3}>{link.maxHits}</Num> opens</Text>
            </View>
            <Press label="Send this link" onPress={() => send(link.readUrl)} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center" }}>
                <Text size={14} tone={color.ink2}>Send</Text>
            </Press>
            <Press label="Turn this link off" disabled={revoking} onPress={onRevoke} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center" }}>
                {revoking ? <ActivityIndicator size="small" color={color.ink3} /> : <Text size={14} tone={color.ink2}>Turn off</Text>}
            </Press>
        </View>
    );
}

export function ShareSheet({ podId, path, title, lemmaName, open, onClose }: {
    podId: string;
    path: string;
    title: string;
    lemmaName: string;
    open: boolean;
    onClose: () => void;
}) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const [life, setLife] = useState(10800);
    const [opens, setOpens] = useState(50);
    const [made, setMade] = useState<SharedLink | null>(null);
    const [making, setMaking] = useState(false);
    const inside = useQuery({ queryKey: keyOf("app-url", path), queryFn: () => source.appUrl(podId, path), enabled: open, staleTime: 10 * 60_000 });
    const links = useQuery({ queryKey: keyOf("links", path), queryFn: () => source.links(podId, path), enabled: open });
    const mint = useMutation({
        mutationFn: () => source.share(podId, path, { expiresSeconds: life, maxHits: opens }),
        onSuccess: (link) => {
            setMade(link);
            setMaking(false);
            void cache.invalidateQueries({ queryKey: keyOf("links", path) });
        },
    });
    const revoke = useMutation({
        mutationFn: (code: string) => source.revoke(podId, code),
        onSettled: () => void cache.invalidateQueries({ queryKey: keyOf("links", path) }),
    });
    const live = (links.data ?? []).filter((one) => Date.parse(one.expiresAt) > Date.now());
    const close = () => { setMade(null); setMaking(false); mint.reset(); onClose(); };

    return (
        <Sheet open={open} onClose={close} title={`Share ${title}`} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8 }} keyboardShouldPersistTaps="handled">
                <View style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: TOUCH + 12 }}>
                    <FilesIcon name="link" size={20} tone={color.ink} />
                    <View style={{ flex: 1, minWidth: 0 }}>
                        <Text size={16}>People in {lemmaName}</Text>
                        <Text size={13} tone={color.ink3}>They sign in to open it. Anyone else is asked to join first.</Text>
                    </View>
                    <Button title="Send" kind="outline" height={36} size={14} disabled={!inside.data} onPress={() => inside.data && send(inside.data)} />
                </View>

                <View style={{ borderTopWidth: 1, borderTopColor: color.line, marginTop: 6, paddingTop: 12, gap: 12 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
                        <FilesIcon name="web" size={20} tone={color.ink} />
                        <View style={{ flex: 1, minWidth: 0 }}>
                            <Text size={16}>Anyone with the link</Text>
                            <Text size={13} tone={color.ink3}>
                                {live.length === 0 ? "A read-only link that expires." : live.length === 1 ? "One link is live." : `${live.length} links are live.`}
                            </Text>
                        </View>
                        {!making && !made ? <Button title={live.length ? "New link" : "Make one"} kind="outline" height={36} size={14} onPress={() => setMaking(true)} /> : null}
                    </View>

                    {making ? (
                        <View style={{ gap: 12, paddingLeft: 34 }}>
                            <Text size={13} tone={color.ink3}>Lasts</Text>
                            <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
                                {LIVES.map((one) => <Choice key={one.seconds} label={one.label} chosen={life === one.seconds} onPress={() => setLife(one.seconds)} />)}
                            </View>
                            <Text size={13} tone={color.ink3}>Stops after</Text>
                            <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
                                {OPENS.map((hits) => <Choice key={hits} label={`${hits} opens`} chosen={opens === hits} onPress={() => setOpens(hits)} />)}
                            </View>
                            <View style={{ flexDirection: "row", gap: 10 }}>
                                <Button title="Cancel" kind="outline" grow onPress={() => setMaking(false)} />
                                <Button title="Make the link" grow busy={mint.isPending} onPress={() => mint.mutate()} />
                            </View>
                            {mint.isError ? <Text size={13.5} tone={color.amber}>{mint.error instanceof Error ? mint.error.message : "That link could not be made."}</Text> : null}
                        </View>
                    ) : null}

                    {made ? (
                        <View style={{ gap: 10, paddingLeft: 34 }}>
                            <Text size={15} numberOfLines={1}>{made.readUrl.replace(/^https?:\/\//, "")}</Text>
                            <Text size={13.5} tone={color.ink2} style={{ lineHeight: 19 }}>
                                Works for {hoursLeft(made.expiresAt)} and <Num size={13.5} tone={color.ink2}>{made.maxHits}</Num> opens. Every view counts, including yours.
                            </Text>
                            <View style={{ flexDirection: "row", gap: 10 }}>
                                <Button title="See what they see" kind="outline" grow onPress={() => void WebBrowser.openBrowserAsync(made.readUrl)} />
                                <Button title="Send" grow onPress={() => send(made.readUrl)} />
                            </View>
                        </View>
                    ) : null}

                    {live.filter((one) => one.code !== made?.code).length > 0 ? (
                        <View style={{ marginTop: 2 }}>
                            {live.filter((one) => one.code !== made?.code).map((link) => (
                                <LiveLink key={link.code} link={link} revoking={revoke.isPending && revoke.variables === link.code} onRevoke={() => revoke.mutate(link.code)} />
                            ))}
                        </View>
                    ) : null}
                    {links.isError ? <Text size={13.5} tone={color.amber}>The links could not be read just now.</Text> : null}
                </View>
            </ScrollView>
        </Sheet>
    );
}
