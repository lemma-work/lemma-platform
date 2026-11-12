import { useState, type ReactNode } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma } from "../../../data/hooks";
import { LemmaFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { Group, ReadProblem, Reading, Say, SettingsScreen } from "../ui";
import { useAskable, useConnect, useLinks, useMayConnect, useShareableFolders, useShareableTables } from "./hooks";
import { connectProblem, type LinkShare } from "./model";
import { askerChoices, sameShare, seededShares, shareRows, sharesNotListed, toggled, type ShareRow } from "@web/space/pod-links";

/** Back goes to the part this screen was opened from. */
const BACK = lemmaPart("works-with")?.label ?? "Settings";

/** Letting another Lemma ask this one, or changing what one already allowed
 *  reads (the web's Connect dialog, `space/connect-teammate.tsx`). Made from
 *  the side being asked, because that side decides: whoever may change who
 *  has access here lets the other in and picks which tables and folders it
 *  may read. The other then asks with nobody present, and reads only what was
 *  shared plus what is Public here. */
export function ConnectScreen({ podId, asker: editing }: PartProps & { asker?: string }) {
    const router = useRouter();
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "this Lemma";
    const may = useMayConnect(podId);
    const [asker, setAsker] = useState(editing ?? "");
    /* Null until somebody ticks or unticks something: until then it is what
       the chosen Lemma reads already. Connecting again replaces every share,
       so starting from nothing would take away what it read before. */
    const [picked, setPicked] = useState<LinkShare[] | null>(null);
    const [opened, setOpened] = useState<string[]>([]);
    const [problem, setProblem] = useState<string | null>(null);

    const askable = useAskable(podId);
    const links = useLinks(podId);
    const tables = useShareableTables(podId);
    const folderPaths = ["/", ...opened];
    const folderLists = useShareableFolders(podId, folderPaths);
    const folderRows = new Map(folderPaths.map((path, at) => [path, shareRows(folderLists[at]?.data ?? [])]));
    const connect = useConnect(podId);

    const choices = askerChoices(askable.data ?? [], links.data ?? []);
    const chosen = choices.find((other) => other.id === asker);
    const shared = picked ?? seededShares(links.data ?? [], asker);
    const tableRows = shareRows(tables.data ?? []);
    const listed = [...tableRows, ...[...folderRows.values()].flat()].map((row) => row.share);
    const unlisted = sharesNotListed(shared, listed);
    const listing = tables.isPending || Boolean(folderLists[0]?.isPending);
    const readsFrom = askable.isSuccess && links.isSuccess;

    const choose = (id: string) => { setAsker(id); setPicked(null); setProblem(null); };
    const toggle = (one: LinkShare) => setPicked(toggled(shared, one));
    const open = (path: string) => setOpened((was) => (was.includes(path) ? was.filter((one) => one !== path) : [...was, path]));
    const save = () => {
        setProblem(null);
        connect.mutate({ asker, shares: shared }, {
            onSuccess: () => router.back(),
            onError: (error) => setProblem(connectProblem(error, name)),
        });
    };

    const title = editing && chosen ? `What ${chosen.name} can read in ${name}` : `Let another Lemma ask ${name}`;
    if (may.answered && !may.allowed) {
        return (
            <SettingsScreen title={title} back={BACK}>
                <Say>Only an admin of {name} can let another Lemma ask it.</Say>
            </SettingsScreen>
        );
    }

    const row = (one: ShareRow, depth: number): ReactNode => {
        const key = `${one.share.kind}:${one.share.name}`;
        const isOpen = one.folder && opened.includes(one.share.name);
        const at = folderPaths.indexOf(one.share.name);
        const inside = isOpen ? folderLists[at] : undefined;
        const children = isOpen ? folderRows.get(one.share.name) ?? [] : [];
        return (
            <View key={key}>
                <ShareLine
                    share={one}
                    depth={depth}
                    ticked={shared.some((item) => sameShare(item, one.share))}
                    open={isOpen}
                    onToggle={() => toggle(one.share)}
                    onOpen={one.folder ? () => open(one.share.name) : undefined}
                />
                {isOpen ? (
                    <View style={{ paddingLeft: 22 * (depth + 1) }}>
                        {inside?.isPending ? <Reading /> : null}
                        {inside?.isError ? <Say tone="bad">Couldn’t open this folder.</Say> : null}
                        {inside?.isSuccess && children.length === 0 ? <Say>No folders inside.</Say> : null}
                    </View>
                ) : null}
                {children.map((child) => row(child, depth + 1))}
            </View>
        );
    };

    return (
        <SettingsScreen title={title} note="It can ask with nobody present, and reads only what you share and what is Public." back={BACK}>
            <Group label="Who can ask">
                {askable.isPending || links.isPending ? <Reading /> : null}
                {askable.isError || links.isError ? (
                    <ReadProblem said="Couldn’t load the Lemmas you can connect." onRetry={() => { void askable.refetch(); void links.refetch(); }} />
                ) : null}
                {editing && readsFrom && !chosen ? (
                    <Say>
                        You’re not in {links.data?.find((link) => link.podId === editing)?.name ?? "that Lemma"}, so you can’t change what it reads. You can still disconnect it.
                    </Say>
                ) : null}
                {readsFrom && choices.length === 0 ? (
                    <Say>There’s no other Lemma you’re in. You can only connect one you’re in yourself.</Say>
                ) : null}
                {choices.map((other, index) => {
                    const on = other.id === asker;
                    return (
                        <Press
                            key={other.id}
                            role="radio"
                            label={other.name}
                            accessibilityState={{ selected: on }}
                            onPress={() => choose(other.id)}
                            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: other.connected ? 58 : 50, paddingVertical: 6, borderBottomWidth: index === choices.length - 1 ? 0 : 1, borderBottomColor: color.line }}
                        >
                            <LemmaFace id={other.id} icon={other.iconUrl} size={28} />
                            <View style={{ flex: 1, gap: 1 }}>
                                <Text size={16}>{other.name}</Text>
                                {other.connected ? <Text size={13} tone={color.ink3}>Already allowed. Choosing it changes what it reads.</Text> : null}
                            </View>
                            {on ? <Icon name="check" size={18} width={2} /> : null}
                        </Press>
                    );
                })}
            </Group>

            <Group label="What it can read">
                <Text size={13.5} tone={color.ink2} style={{ lineHeight: 19, paddingBottom: 4 }}>
                    {chosen
                        ? `Anyone in ${chosen.name}, and its automations with nobody present, will be able to read what you tick here, and anything Public in ${name}.`
                        : "Whoever is in the Lemma you choose, and its automations, will be able to read what you tick here."}
                </Text>
                {listing ? <Reading /> : null}
                {tables.isError || folderLists[0]?.isError ? (
                    <ReadProblem said="Couldn’t load everything there is to share." onRetry={() => { void tables.refetch(); void folderLists[0]?.refetch(); }} />
                ) : null}
                {!listing && tableRows.length === 0 && (folderRows.get("/") ?? []).length === 0 && unlisted.length === 0 ? (
                    <Say>Nothing here to share yet. It will read only what is Public.</Say>
                ) : null}
                {/* Shared already and in no list on screen: a folder deeper than
                    anything opened. Drawn ticked so saving keeps it. */}
                {unlisted.map((one) => row({ share: one, public: false, folder: false }, 0))}
                {tableRows.map((one) => row(one, 0))}
                {(folderRows.get("/") ?? []).map((one) => row(one, 0))}
            </Group>

            {problem ? <Say tone="bad">{problem}</Say> : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Cancel" kind="outline" grow onPress={() => router.back()} />
                <Button
                    title={connect.isPending ? "Saving…" : !chosen ? "Connect" : chosen.connected ? `Save what ${chosen.name} reads` : `Let ${chosen.name} ask`}
                    grow
                    busy={connect.isPending}
                    disabled={!chosen || !links.isSuccess}
                    onPress={save}
                />
            </View>
        </SettingsScreen>
    );
}

/** One table or folder that could be shared: a tick, what it is, and, for a
 *  folder, a way to open it and share a folder inside instead. */
function ShareLine({ share, depth, ticked, open, onToggle, onOpen }: {
    share: ShareRow;
    depth: number;
    ticked: boolean;
    open: boolean;
    onToggle: () => void;
    onOpen?: () => void;
}) {
    const what = share.share.kind === "table" ? "Table" : "Folder, and everything in it";
    return (
        <View style={{ flexDirection: "row", alignItems: "center", paddingLeft: 22 * depth, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <Press
                label={share.share.name}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: ticked }}
                onPress={onToggle}
                style={{ flex: 1, flexDirection: "row", alignItems: "center", gap: 12, minHeight: 56, paddingVertical: 6 }}
            >
                <View style={{ width: 22, height: 22, borderRadius: 6, borderWidth: ticked ? 0 : 1.5, borderColor: color.line2, backgroundColor: ticked ? color.ink : "transparent", alignItems: "center", justifyContent: "center" }}>
                    {ticked ? <Icon name="check" size={15} width={2.2} tone={color.onInk} /> : null}
                </View>
                <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                    <Text size={16} numberOfLines={1}>{share.share.name}</Text>
                    <Text size={13} tone={color.ink3} numberOfLines={2}>
                        {share.public ? `${what} · Public: it can read this already` : what}
                    </Text>
                </View>
            </Press>
            {onOpen ? (
                <Press
                    label={`${open ? "Close" : "Open"} ${share.share.name}`}
                    accessibilityState={{ expanded: open }}
                    onPress={onOpen}
                    style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}
                >
                    <Icon name={open ? "down" : "forward"} size={16} tone={color.ink3} />
                </Press>
            ) : null}
        </View>
    );
}
