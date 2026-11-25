import { useEffect, useMemo, useState } from "react";
import { KeyboardAvoidingView, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { lemmaSays } from "@web/outcomes/model";
import { useApps, useLemma, useLemmas, useMe, useNeeds, useOpenLemma, useSections } from "../../../../data/hooks";
import { routes } from "../../../../data/routes";
import type { AppItem } from "../../../../data/types";
import { adminRoutes } from "../../../../areas/admin/routes";
import type { LemmaPartId } from "../../../../areas/admin/sections";
import { useMembers, useSurfaces } from "../../../../areas/work/hooks";
import { workRoutes } from "../../../../areas/work/routes";
import { NotYours } from "../../../../areas/more/begin/not-yours";
import { ShareSheet } from "../../../../areas/more/share/sheet";
import { FilesIcon } from "../../../../areas/files/glyphs";
import { AppTile } from "../../../../ui/app-tile";
import { AskBox } from "../../../../ui/ask-box";
import { chatRoutes } from "../../../../areas/chat/routes";
import { holdFiles } from "../../../../thread/hand-over";
import { BackLink, IconButton, Loading } from "../../../../ui/chrome";
import { PersonFace } from "../../../../ui/face";
import { Icon, type IconName } from "../../../../ui/icons";
import { NeedRow } from "../../../../ui/need-card";
import { Press } from "../../../../ui/press";
import { SideRowLine } from "../../../../ui/rows";
import { Scene, type PlaceKey, type ScenePlace } from "../../../../ui/scene";
import { Num, Text } from "../../../../ui/text";
import { color, TOUCH } from "../../../../ui/tokens";

/** A Lemma's own page, as the web draws it (`outcomes/lemma-page.tsx`) in
 *  one column: its scene -- the face on its tint, its apps round it, the
 *  places it can be reached at its feet, you beside it, each one a button --
 *  then what it says in its own voice, the box to ask it, what needs you
 *  from it, its apps, its chats (the web keeps those in the sidebar), and
 *  what it holds and how it works, a row each.
 *
 *  Opening it makes it the one Home unfolds, as the web's sidebar unfolds
 *  the Lemma you are in. */

const PLACES: readonly PlaceKey[] = ["EMAIL", "WHATSAPP", "TELEGRAM", "SLACK"];
const PLATFORM_OF: Record<string, PlaceKey> = { EMAIL: "EMAIL", RESEND: "EMAIL", WHATSAPP: "WHATSAPP", TELEGRAM: "TELEGRAM", SLACK: "SLACK" };
const PLACE_NAME: Record<PlaceKey, string> = { EMAIL: "email", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", SLACK: "Slack" };

interface Part {
    key: string;
    label: string;
    line: string;
    icon: IconName;
    href: string;
    warm?: boolean;
}

export default function LemmaPage() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string }>();
    const podId = String(params.podId);
    const lemma = useLemma(podId);
    const me = useMe();
    const { pending: listing } = useLemmas();
    const { sections } = useSections();
    const { needs } = useNeeds();
    const { apps } = useApps();
    const surfaces = useSurfaces(podId);
    const members = useMembers(podId);
    const { setOpen } = useOpenLemma();
    const [sharing, setSharing] = useState(false);
    useEffect(() => {
        if (lemma) setOpen(lemma.id);
    }, [lemma, setOpen]);

    const now = Date.now();
    const section = sections.find((one) => one.lemma.id === podId) ?? null;
    const rows = section?.rows ?? [];
    const mine = needs.filter((item) => item.podId === podId).sort((a, b) => a.sinceMs - b.sinceMs);
    const ownApps = apps.filter((app) => app.podId === podId);
    const chats = rows.filter((row) => row.kind === "chat" || row.kind === "line" || row.kind === "shared");
    const running = rows.filter((row) => row.running).length;
    const automations = rows.filter((row) => row.kind === "automation").length;
    const people = members.data ?? [];
    const places: ScenePlace[] = useMemo(() => {
        const on = new Set<PlaceKey>(["EMAIL"]);
        for (const surface of surfaces.data ?? []) {
            const key = PLATFORM_OF[surface.platform.toUpperCase()];
            if (key && surface.active) on.add(key);
        }
        return PLACES.map((key) => ({ key, on: on.has(key) }));
    }, [surfaces.data]);
    const reachLine = places.filter((place) => place.on).map((place) => PLACE_NAME[place.key]).join(", ");
    const first = me.data?.firstName;
    const hour = new Date(now).getHours();
    const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const says = `${greeting}${first ? ", " + first : ""}. ` + lemmaSays({ empty: rows.length === 0 && ownApps.length === 0, running, needs: mine.length });

    if (!lemma) {
        return (
            <SafeAreaView style={{ flex: 1, backgroundColor: color.paper }}>
                <View style={{ paddingHorizontal: 6 }}><BackLink label="Home" /></View>
                {/* Not in the list once it has been read: a link to a Lemma
                    that is not one of yours, and the way to ask in (area G). */}
                {listing ? <Loading /> : <View style={{ paddingHorizontal: 20 }}><NotYours key={podId} podId={podId} /></View>}
            </SafeAreaView>
        );
    }

    const part = (id: LemmaPartId) => adminRoutes.lemmaSection(podId, id);
    const holds: Part[] = [
        { key: "files", label: "Files", line: "Everything handed to it", icon: "file", href: routes.lemma(podId, "files") },
        { key: "tables", label: "Tables", line: "What it keeps track of", icon: "table", href: routes.lemma(podId, "tables") },
        { key: "memory", label: "Memory", line: "Notes it wrote itself", icon: "bookmark", href: part("memory") },
        { key: "skills", label: "Skills", line: "How you do things, written down", icon: "book", href: part("skills") },
    ];
    const works: Part[] = [
        { key: "automations", label: "Automations", line: automations ? `${automations} ran lately · What it does on its own` : "What it does on its own", icon: "clock", href: workRoutes.runs(podId) },
        { key: "agents", label: "Agents", line: "Helpers it hands work to", icon: "agent", href: workRoutes.runs(podId, "agents") },
        { key: "connections", label: "Connections", line: "What it works through", icon: "plug", href: part("connectors") },
        { key: "reach", label: "Where to reach it", line: reachLine ? reachLine.charAt(0).toUpperCase() + reachLine.slice(1) : "WhatsApp, Slack, email and more", icon: "chat", href: part("channels") },
        { key: "people", label: "People", line: people.length ? peopleLine(people.map((one) => one.name)) : "Who can open it", icon: "people", href: part("people") },
        { key: "model", label: "Default model", line: "What it thinks with", icon: "spark", href: part("model") },
        { key: "mcp", label: "MCP", line: "ChatGPT, Claude, Claude Code", icon: "link", href: part("mcp") },
        { key: "works-with", label: "Other Lemmas", line: "Other Lemmas it can ask", icon: "lemmas", href: part("works-with") },
    ];
    const status = running > 0
        ? { dot: color.violet, text: `Working on ${running} ${running === 1 ? "thing" : "things"}` }
        : mine.length > 0 ? { dot: color.warm, text: "Waiting on you" } : { dot: color.ok, text: "Ready" };

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 8 }}>
                    <BackLink label="Home" onPress={() => (router.canGoBack() ? router.back() : router.replace(routes.home as never))} />
                    <View style={{ flex: 1 }} />
                    {people.length > 0 ? (
                        <Press label={`People in ${lemma.name}: ${people.length}`} onPress={() => router.push(part("people") as never)} style={{ flexDirection: "row", alignItems: "center", height: TOUCH, paddingHorizontal: 4 }}>
                            {people.slice(0, 2).map((person, index) => (
                                <View key={person.id} style={{ marginLeft: index === 0 ? 0 : -7, borderRadius: 14, borderWidth: 2, borderColor: color.paper }}>
                                    <PersonFace name={person.name} size={26} />
                                </View>
                            ))}
                            {people.length > 2 ? (
                                <View style={{ marginLeft: -7, height: 30, minWidth: 30, paddingHorizontal: 5, borderRadius: 15, borderWidth: 2, borderColor: color.paper, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>
                                    <Num size={11} tone={color.ink2}>{`+${people.length - 2}`}</Num>
                                </View>
                            ) : null}
                        </Press>
                    ) : null}
                    <Press label={"Share or export " + lemma.name} onPress={() => setSharing(true)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                        <FilesIcon name="share" size={20} tone={color.ink} />
                    </Press>
                    <IconButton name="more" label="About, who has access, where to reach it, settings" onPress={() => router.push(routes.lemma(podId, "settings") as never)} />
                </View>
                <ShareSheet podId={podId} subject={{ kind: "lemma" }} open={sharing} onClose={() => setSharing(false)} />

                <ScrollView contentContainerStyle={{ paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
                    <View style={{ alignItems: "center", paddingTop: 22 }}>
                        <Scene
                            lemma={lemma}
                            apps={ownApps}
                            places={places}
                            people={Math.max(1, people.length)}
                            me={me.data?.name ?? "You"}
                            onApp={(app) => router.push(routes.app(app.podId, app.name) as never)}
                            onAddApp={() => router.push(routes.newChat(podId) as never)}
                            onPlace={() => router.push(part("channels") as never)}
                            onPeople={() => router.push(part("people") as never)}
                        />
                        <Text accessibilityRole="header" size={24} weight="medium" style={{ marginTop: 14, letterSpacing: -0.24 }}>{lemma.name}</Text>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 7, marginTop: 4 }}>
                            <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: status.dot }} />
                            <Text size={13.5} tone={color.ink3}>{status.text}</Text>
                        </View>
                    </View>

                    <Text size={21} weight="medium" style={{ textAlign: "center", lineHeight: 27, letterSpacing: -0.3, paddingHorizontal: 28, paddingTop: 18, paddingBottom: 18 }}>{says}</Text>

                    <View style={{ paddingHorizontal: 16 }}>
                        <AskBox
                            lemmas={[lemma]}
                            podId={podId}
                            fixed
                            attach
                            placeholder={`Ask ${lemma.name} anything`}
                            onPick={() => undefined}
                            onSend={(_podId, text, files) => router.push(chatRoutes.start(podId, text, holdFiles(files)) as never)}
                        />
                    </View>

                    {mine.length > 0 ? (
                        <View style={{ marginTop: 28 }}>
                            <Heading title="Needs you" count={mine.length} more={mine.length > 3 ? { label: "All", href: routes.needs } : undefined} />
                            {mine.slice(0, 3).map((item, index) => (
                                <NeedRow key={item.key} item={item} lemma={lemma} now={now} inLemma first={index === 0} />
                            ))}
                        </View>
                    ) : null}

                    <View style={{ marginTop: 28 }}>
                        <Heading title="Apps" count={ownApps.length || undefined} />
                        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 12, paddingHorizontal: 16 }}>
                            {ownApps.map((app: AppItem) => (
                                <View key={app.id} style={{ width: 240 }}><AppTile app={app} /></View>
                            ))}
                            <Press label="Add an app" onPress={() => router.push(routes.newChat(podId) as never)} style={{ width: ownApps.length ? 120 : 240, gap: 7 }}>
                                <View style={{ aspectRatio: ownApps.length ? 0.8 : 1.6, borderRadius: 12, borderWidth: 1.5, borderStyle: "dashed", borderColor: color.line2, alignItems: "center", justifyContent: "center" }}>
                                    <Icon name="plus" size={24} tone={color.ink3} />
                                </View>
                                <Text size={14.5} tone={color.ink2}>Add an app</Text>
                            </Press>
                        </ScrollView>
                    </View>

                    <View style={{ marginTop: 28 }}>
                        <Heading title="Chats" more={{ label: "All", href: routes.lemma(podId, "chats") }} />
                        {chats.length === 0 ? <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 20 }}>No chats yet. Ask {lemma.name} anything above.</Text> : null}
                        {chats.slice(0, 4).map((row) => <SideRowLine key={row.key} row={row} now={now} pad={16} glyphWidth={26} />)}
                    </View>

                    <Parts title={`What ${lemma.name} holds`} parts={holds} />
                    <Parts title={`How ${lemma.name} works`} parts={works} />
                </ScrollView>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}

function peopleLine(names: readonly string[]): string {
    const firsts = names.map((name) => name.split(/\s+/)[0]);
    return firsts.length <= 2 ? firsts.join(", ") : `${firsts.slice(0, 2).join(", ")} +${firsts.length - 2}`;
}

function Heading({ title, count, more }: { title: string; count?: number; more?: { label: string; href: string } }) {
    const router = useRouter();
    return (
        <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8, paddingHorizontal: 20, paddingBottom: 10 }}>
            <Text accessibilityRole="header" size={19} weight="medium" style={{ letterSpacing: -0.19 }}>{title}</Text>
            {count ? <Num size={14} tone={color.ink3}>{count}</Num> : null}
            <View style={{ flex: 1 }} />
            {more ? (
                <Press role="link" label={`${title}: ${more.label}`} onPress={() => router.push(more.href as never)} hitSlop={10}>
                    <Text size={14} tone={color.ink2}>{more.label}</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** A card grid on the web, rows on a phone: an icon on the Lemma's light,
 *  what it is, one line on what is in it, and the way in. */
function Parts({ title, parts }: { title: string; parts: readonly Part[] }) {
    const router = useRouter();
    return (
        <View style={{ marginTop: 30 }}>
            <Heading title={title} />
            <View style={{ marginHorizontal: 16, borderRadius: 16, borderWidth: 1, borderColor: color.line2, backgroundColor: color.paper, overflow: "hidden" }}>
                {parts.map((one, index) => (
                    <Press
                        key={one.key}
                        role="link"
                        label={`${one.label}. ${one.line}`}
                        onPress={() => router.push(one.href as never)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 62, paddingLeft: 14, paddingRight: 12, borderTopWidth: index === 0 ? 0 : 1, borderTopColor: color.line }}
                    >
                        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: color.tint, alignItems: "center", justifyContent: "center" }}>
                            <Icon name={one.icon} size={19} tone={color.ink} />
                        </View>
                        <View style={{ flex: 1, minWidth: 0 }}>
                            <Text size={15.5}>{one.label}</Text>
                            <Text size={13} tone={one.warm ? color.warm : color.ink3} numberOfLines={1}>{one.line}</Text>
                        </View>
                        <Icon name="forward" size={17} tone={color.ink3} />
                    </Press>
                ))}
            </View>
        </View>
    );
}
