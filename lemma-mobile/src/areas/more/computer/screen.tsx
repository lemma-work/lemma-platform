import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ActivityIndicator, BackHandler, KeyboardAvoidingView, Platform, RefreshControl, ScrollView, useWindowDimensions, View, type LayoutRectangle } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useIsFocused, useLocalSearchParams } from "expo-router";
import { BackLink } from "../../../ui/chrome";
import { Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { problemOf } from "../../admin/words";
import { FilesPlace } from "./files";
import { useBrowserState, useChatFolder, useComputerSource, useFiles, useOpenInBrowser, useStatus } from "./hooks";
import { glassTokens, LiveScreen } from "./live-screen";
import { liveWords, opensInBrowser, SAMPLE_SCREEN, startingFolder, startupLine, stateLine, WHOSE, type Dot } from "./model";
import { SitesPlace } from "./sites";
import { rootsOf, type Entry } from "@web/computer/machine";
import type { LiveState } from "@web/computer/live";

/** The computer the Lemmas work on (PARITY.md, G; the web's
 *  `computer-view.tsx`): the person's own, one for them, shared by every
 *  Lemma. Opened from a chat, it starts on that chat's folder.
 *
 *  Looking costs nothing: the listing does not wake it, the browser's
 *  status never starts one, and every read stops while this screen is not
 *  the one in front. Waking it, opening a file and showing the screen are
 *  the only things that cost anything, and each is a press. */

type Place = "files" | "sites";

const PLACES: { key: Place; label: string }[] = [
    { key: "files", label: "Files" },
    { key: "sites", label: "Signed-in sites" },
];

export function ComputerScreen() {
    const params = useLocalSearchParams<{ pod?: string; conversation?: string }>();
    const podId = typeof params.pod === "string" && params.pod ? params.pod : null;
    const conversationId = typeof params.conversation === "string" && params.conversation ? params.conversation : null;
    const source = useComputerSource();
    const focused = useIsFocused();
    const { width } = useWindowDimensions();

    const [place, setPlace] = useState<Place>("files");
    const [wake, setWake] = useState(false);
    const [at, setAt] = useState<string | null>(null);
    const [open, setOpen] = useState<Entry | null>(null);
    const [hidden, setHidden] = useState(false);
    const [watching, setWatching] = useState(false);
    const [full, setFull] = useState(false);
    const [live, setLive] = useState<LiveState>("connecting");
    const [frame, setFrame] = useState<LayoutRectangle | null>(null);

    const chat = useChatFolder(podId, conversationId);
    const status = useStatus();
    const startup = startupLine(status.data);
    /* Wait for the chat's folder before listing, rather than flash the
       server's default first. */
    const finding = Boolean(podId && conversationId) && chat.isPending;
    const asked = startingFolder(at, chat.data);
    const listing = useFiles(asked, wake, !startup && !finding);
    const first = listing.data?.pages[0];
    const roots = rootsOf(first);
    const settled = !listing.isPlaceholderData;
    const path = (settled ? first?.path : null) ?? asked ?? roots.workspace;
    /* Asked to wake, and the answer is not back yet. */
    const waking = wake && Boolean(first?.sleeping) && listing.isFetching;
    /* Not asked until the listing has said the computer is up. */
    const browser = useBrowserState(first !== undefined && !first.sleeping);
    const line = stateLine(first, browser.data, listing.isPending);
    const tab = useOpenInBrowser();

    /* Stop watching the moment nobody can see it, or the computer went back
       to sleep under it: the socket is what keeps it awake. */
    useEffect(() => {
        if (!focused || line.state === "asleep" || line.state === "checking") {
            setWatching(false);
            setFull(false);
        }
    }, [focused, line.state]);

    useFocusEffect(useCallback(() => {
        const back = BackHandler.addEventListener("hardwareBackPress", () => {
            if (full) {
                setFull(false);
                return true;
            }
            if (open) {
                setOpen(null);
                return true;
            }
            return false;
        });
        return () => back.remove();
    }, [full, open]));

    const goTo = (next: string) => {
        setAt(next);
        setOpen(null);
    };
    /* Waking is the listing asked with `wake`; the status is asked again a
       moment later so the download and the start are seen, not missed
       between two slow polls. */
    const wakeIt = () => {
        setWake(true);
        setTimeout(() => void status.refetch(), 1_500);
    };
    const show = () => {
        setLive("connecting");
        setWatching(true);
    };
    const stop = () => {
        setWatching(false);
        setFull(false);
    };

    const glass = glassTokens();
    const boxWidth = width - 40;
    const boxHeight = watching ? Math.round(boxWidth * 0.66) : 112;
    const refresh = () => {
        void listing.refetch();
        void status.refetch();
    };

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} enabled={full} style={{ flex: 1 }}>
                <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6 }}>
                        <BackLink />
                    </View>
                    <View style={{ paddingHorizontal: 20, gap: 4, paddingBottom: 12 }}>
                        <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24 }}>Your computer</Text>
                        <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{WHOSE}</Text>
                    </View>

                    <View style={{ paddingHorizontal: 20, paddingBottom: 10 }}>
                        {startup ? <Startup progress={startup} /> : <StateRow dot={line.dot} headline={line.headline} note={line.note} />}
                    </View>

                    <View
                        onLayout={(event) => setFrame(event.nativeEvent.layout)}
                        style={{ marginHorizontal: 20, height: boxHeight, borderRadius: 14, backgroundColor: glass.ground, borderWidth: 1, borderColor: color.line2, overflow: "hidden", alignItems: "center", justifyContent: "center", paddingHorizontal: 18 }}
                    >
                        {watching ? null : !source.machine ? (
                            <Text size={14} tone={glass.quiet} style={{ textAlign: "center", lineHeight: 20 }}>{SAMPLE_SCREEN}</Text>
                        ) : waking ? (
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                                <ActivityIndicator size="small" color={glass.quiet} />
                                <Text size={14} tone={glass.quiet}>Waking it…</Text>
                            </View>
                        ) : line.action === "wake" ? (
                            <GlassButton title="Wake it" onPress={wakeIt} />
                        ) : line.action === "show" && !startup ? (
                            <GlassButton title="Show the screen" onPress={show} />
                        ) : startup || line.state === "checking" ? (
                            <ActivityIndicator color={glass.quiet} />
                        ) : null /* Nothing to show and nothing to press: an idle glass, said in the line above. */}
                    </View>

                    {watching ? (
                        <View style={{ paddingHorizontal: 20, paddingTop: 8, gap: 4 }}>
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
                                <Text accessibilityLiveRegion="polite" size={13} tone={color.ink2} style={{ flex: 1, lineHeight: 18 }}>{liveWords(live)}</Text>
                                <Quick label="Full screen" onPress={() => setFull(true)} strong />
                                <Quick label="Stop" onPress={stop} />
                            </View>
                            {opensInBrowser(live) ? (
                                <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                                    {tab.isPending ? <ActivityIndicator size="small" color={color.ink3} /> : <Quick label="Open it in the browser instead" onPress={() => tab.mutate()} strong />}
                                </View>
                            ) : null}
                            {tab.isError ? <Text size={13} tone={color.amber}>{problemOf(tab.error, "Couldn’t open the browser. This computer may not have one to show.")}</Text> : null}
                        </View>
                    ) : null}

                    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0, marginTop: 14 }} contentContainerStyle={{ gap: 6, paddingHorizontal: 20, paddingBottom: 8 }}>
                        {PLACES.map((one) => {
                            const on = one.key === place;
                            return (
                                <Press
                                    key={one.key}
                                    role="tab"
                                    label={one.label}
                                    accessibilityState={{ selected: on }}
                                    onPress={() => setPlace(one.key)}
                                    hitSlop={{ top: 6, bottom: 6 }}
                                    style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, backgroundColor: on ? color.ink : color.paper, borderWidth: on ? 0 : 1, borderColor: color.line2, justifyContent: "center" }}
                                >
                                    <Text size={13.5} tone={on ? color.onInk : color.ink}>{one.label}</Text>
                                </Press>
                            );
                        })}
                    </ScrollView>

                    <ScrollView
                        style={{ flex: 1 }}
                        contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 32 }}
                        keyboardShouldPersistTaps="handled"
                        refreshControl={place === "files" ? <RefreshControl refreshing={false} onRefresh={refresh} tintColor={color.ink3} /> : undefined}
                    >
                        {place === "files" ? (
                            startup ? (
                                <Text size={15} tone={color.ink2} style={{ lineHeight: 21, paddingVertical: 8 }}>Its files are here once it has started.</Text>
                            ) : (
                                <FilesPlace
                                    listing={listing}
                                    path={path}
                                    roots={roots}
                                    open={open}
                                    hidden={hidden}
                                    onGo={goTo}
                                    onOpen={setOpen}
                                    onShowHidden={setHidden}
                                    onWake={wakeIt}
                                />
                            )
                        ) : <SitesPlace enabled={focused && place === "sites"} onWoke={() => setTimeout(() => void status.refetch(), 1_500)} />}
                    </ScrollView>

                    {watching && focused && frame ? (
                        <View
                            style={full
                                ? { position: "absolute", left: 0, top: 0, right: 0, bottom: 0, backgroundColor: glass.ground }
                                : { position: "absolute", left: frame.x, top: frame.y, width: frame.width, height: frame.height, borderRadius: 14, overflow: "hidden", backgroundColor: glass.ground }}
                        >
                            <LiveScreen conversationId={conversationId} bar={full ? { done: true } : null} onState={setLive} onDone={() => setFull(false)} />
                            {full && live !== "live" ? (
                                <View pointerEvents="none" style={{ position: "absolute", left: 0, right: 0, top: 0, padding: 16 }}>
                                    <Text size={13.5} tone={glass.quiet} style={{ textAlign: "center" }}>{liveWords(live)}</Text>
                                </View>
                            ) : null}
                        </View>
                    ) : null}
                </View>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}

const DOT_TONE: Record<Dot, () => string> = {
    violet: () => color.violet,
    amber: () => color.amber,
    on: () => color.ink2,
    off: () => color.line2,
};

/** The dot, the headline and the note, on one line. */
function StateRow({ dot, headline, note }: { dot: Dot; headline: string; note: string }) {
    return (
        <View accessibilityLabel={`${headline}. ${note}`} style={{ flexDirection: "row", alignItems: "flex-start", gap: 8, minHeight: 24 }}>
            <View style={{ width: 8, height: 8, borderRadius: 4, marginTop: 6, backgroundColor: DOT_TONE[dot]() }} />
            <Text size={14} tone={color.ink2} style={{ flex: 1, lineHeight: 20 }}>
                <Text size={14.5} weight="medium">{headline}</Text>
                {"  "}{note}
            </Text>
        </View>
    );
}

/** While it downloads or starts: the title, the server's detail, a bar, and
 *  how far the download has got. */
function Startup({ progress }: { progress: NonNullable<ReturnType<typeof startupLine>> }) {
    return (
        <View accessibilityRole="progressbar" accessibilityLabel={progress.title} accessibilityValue={progress.fraction !== null ? { min: 0, max: 100, now: Math.round(progress.fraction * 100) } : undefined} style={{ gap: 6 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <ActivityIndicator size="small" color={color.violet} />
                <Text size={14.5} weight="medium" style={{ flex: 1 }}>{progress.title}</Text>
                {progress.downloaded ? <Num size={13} tone={color.ink3}>{progress.downloaded}</Num> : null}
            </View>
            {progress.detail ? <Text size={13.5} tone={color.ink2} style={{ lineHeight: 19 }}>{progress.detail}</Text> : null}
            <View style={{ height: 4, borderRadius: 999, backgroundColor: color.track, overflow: "hidden" }}>
                {progress.fraction !== null ? <View style={{ width: `${Math.round(progress.fraction * 1000) / 10}%`, height: 4, borderRadius: 999, backgroundColor: color.violet }} /> : null}
            </View>
        </View>
    );
}

/** A press on the glass, in the glass's own tokens. */
export function GlassButton({ title, onPress }: { title: string; onPress: () => void }) {
    const glass = glassTokens();
    return (
        <Press label={title} onPress={onPress} style={{ minHeight: TOUCH, paddingHorizontal: 18, borderRadius: 10, backgroundColor: glass.press, borderWidth: 1, borderColor: glass.line, alignItems: "center", justifyContent: "center" }}>
            <Text size={15} weight="medium" tone={glass.ink}>{title}</Text>
        </Press>
    );
}

export function Quick({ label, onPress, strong = false }: { label: string; onPress: () => void; strong?: boolean }): ReactNode {
    return (
        <Press label={label} onPress={onPress} style={{ minHeight: TOUCH - 4, justifyContent: "center" }}>
            <Text size={13.5} weight={strong ? "medium" : "regular"} tone={strong ? color.ink : color.ink2}>{label}</Text>
        </Press>
    );
}
