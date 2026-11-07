import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { AccessibilityInfo, Animated, Platform, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { routes } from "../../../data/routes";
import { ResourceCard } from "../../../thread/resources";
import { BackLink } from "../../../ui/chrome";
import { LemmaFace } from "../../../ui/face";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { Confirm } from "../../admin/ui";
import { chatRoutes } from "../../chat/routes";
import { CallEngine } from "./engine";
import type { CanvasItem } from "@web/call/canvas";
import { useVoiceCall, type VoiceCall } from "./hooks";
import { deckWhere, shownIndex } from "./model";

/** A voice call with a Lemma: the web's `CallScreen`, for a phone.
 *
 *  The Lemma's face is the whole stage until it shows something, its voice
 *  drawn as a violet ring around it (a call is something running). What it
 *  shows during the call takes the screen, the newest the moment it arrives,
 *  paged back with Previous and Next. Under it, one caption: the line being
 *  said. Mute and End call at the foot. Leaving ends the call, asked first
 *  while it is on; after it, a line says where its chat is.
 *
 *  `?conversation=` is the chat the call grew out of, when it was opened
 *  from one: it goes to the voice as background. The call's sound is an
 *  invisible WebView, mounted only while a call is connecting or on
 *  (`engine.tsx`). */
export function CallScreen() {
    const params = useLocalSearchParams<{ podId: string; conversation?: string }>();
    const podId = String(params.podId);
    const conversationId = params.conversation ? String(params.conversation) : null;
    const call = useVoiceCall(podId, conversationId);
    const router = useRouter();
    const { phase, words } = call;
    const inCall = phase.kind === "connecting" || phase.kind === "live";
    const bare = call.canvas.length === 0 || !inCall;

    /* iOS has no live regions: the line is said when it changes. */
    const said = useRef("");
    useEffect(() => {
        if (Platform.OS !== "ios" || !words.line || said.current === words.line) return;
        said.current = words.line;
        AccessibilityInfo.announceForAccessibility(words.line);
    }, [words.line]);

    const compose = (text: string) => router.push(chatRoutes.filled(podId, text) as never);

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 4 }}>
                <BackLink label={call.name} />
            </View>

            {bare ? (
                <Stage call={call} podId={podId} onOpenChat={(id) => router.push(routes.chat(podId, id) as never)} />
            ) : (
                <>
                    <Head call={call} podId={podId} />
                    <Deck items={call.canvas} podId={podId} onShow={call.looking} onCompose={compose} />
                </>
            )}

            {inCall ? <Caption caption={call.caption} /> : null}
            {inCall ? (
                <View style={{ flexDirection: "row", gap: 10, paddingHorizontal: 16, paddingTop: 6, paddingBottom: 8 }}>
                    <Button title={call.muted ? "Unmute" : "Mute"} kind="outline" grow height={48} disabled={!call.canMute} onPress={call.toggleMute} />
                    {/* Ink, the app's yes: this app has no red, and the filled
                        button is the one that reads as final. */}
                    <Button title="End call" grow height={48} onPress={call.end} />
                </View>
            ) : null}

            {call.engine ? <CallEngine ref={call.engine.ref} onMessage={call.engine.onMessage} /> : null}

            <Confirm
                open={call.leaving}
                title="End the call?"
                body={`Leaving ends the call with ${call.name}. Work it already started carries on.`}
                confirm="End call"
                onConfirm={call.leave}
                onClose={call.stay}
            />
        </SafeAreaView>
    );
}

/** True while the person has asked for less movement: the ring then holds
 *  still and only brightens. */
function useStill(): boolean {
    const [still, setStill] = useState(false);
    useEffect(() => {
        let gone = false;
        void AccessibilityInfo.isReduceMotionEnabled().then((on) => { if (!gone) setStill(on); }, () => undefined);
        const listening = AccessibilityInfo.addEventListener("reduceMotionChanged", setStill);
        return () => {
            gone = true;
            listening.remove();
        };
    }, []);
    return still;
}

/** The Lemma's face, its voice a violet ring around it. */
function VoiceFace({ podId, icon, size, level: source, on }: { podId: string; icon: string | null; size: number; level: VoiceCall["level"]; on: boolean }) {
    const still = useStill();
    const level = useSyncExternalStore(source.subscribe, source.snapshot);
    const loud = useRef(new Animated.Value(0)).current;
    const target = on ? Math.min(1, level) : 0;
    useEffect(() => {
        Animated.timing(loud, { toValue: target, duration: 90, useNativeDriver: true }).start();
    }, [loud, target]);
    const gap = Math.max(4, Math.round(size * 0.07));
    const ring = size + gap * 2;
    const room = Math.round(ring * 1.18);
    return (
        <View style={{ width: room, height: room, alignItems: "center", justifyContent: "center" }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
            {on ? (
                <Animated.View
                    style={{
                        position: "absolute",
                        width: ring,
                        height: ring,
                        /* A circle, as design v2 rings a face: the face
                           stands free of a tile, so a square ring would
                           draw the tile back. */
                        borderRadius: ring / 2,
                        borderWidth: Math.max(2, Math.round(size * 0.025)),
                        borderColor: color.violet,
                        opacity: loud.interpolate({ inputRange: [0, 1], outputRange: [0.3, 1] }),
                        transform: [{ scale: still ? 1 : loud.interpolate({ inputRange: [0, 1], outputRange: [1, 1.15] }) }],
                    }}
                />
            ) : null}
            <LemmaFace id={podId} icon={icon} size={size} />
        </View>
    );
}

/** The whole stage: the face, the words, and what can be done in this phase. */
function Stage({ call, podId, onOpenChat }: { call: VoiceCall; podId: string; onOpenChat: (id: string) => void }) {
    const { phase, words, after } = call;
    const inCall = phase.kind === "connecting" || phase.kind === "live";
    return (
        <ScrollView contentContainerStyle={{ flexGrow: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: 24, paddingVertical: 16, gap: 18 }}>
            <VoiceFace podId={podId} icon={call.icon} size={120} level={call.level} on={inCall} />
            <View style={{ alignItems: "center", gap: 8, maxWidth: 360 }}>
                <Text accessibilityRole="header" size={22} weight="medium" style={{ textAlign: "center", letterSpacing: -0.3 }}>{words.title}</Text>
                {words.line ? (
                    <Text
                        accessibilityLiveRegion="polite"
                        accessibilityRole={words.tone === "bad" ? "alert" : undefined}
                        size={15}
                        tone={words.tone === "bad" ? color.amber : color.ink2}
                        style={{ textAlign: "center", lineHeight: 21 }}
                    >
                        {words.line}
                    </Text>
                ) : null}
                {phase.kind === "live" ? (
                    <Text size={13.5} tone={color.ink3} style={{ textAlign: "center" }}>Anything {call.name} shows you appears here.</Text>
                ) : null}
            </View>

            {after ? (
                <View style={{ alignItems: "center", gap: 2, maxWidth: 360 }}>
                    <Text size={14.5} tone={color.ink2} style={{ textAlign: "center", lineHeight: 20 }}>{after.line}</Text>
                    {after.link && after.chatId ? (
                        <Press role="link" label={after.link} onPress={() => after.chatId && onOpenChat(after.chatId)} style={{ minHeight: TOUCH, justifyContent: "center", paddingHorizontal: 8 }}>
                            <Text size={15} weight="medium" tone={color.violetInk}>{after.link}</Text>
                        </Press>
                    ) : null}
                </View>
            ) : null}

            {phase.kind === "refused" ? (
                <View style={{ flexDirection: "row", gap: 10, width: "100%", maxWidth: 360 }}>
                    <Button title="Try again" kind="outline" grow onPress={() => void call.begin()} />
                    <Button title="Open settings" grow onPress={call.openSettings} />
                </View>
            ) : null}
            {phase.kind === "ended" || phase.kind === "failed" ? (
                <Button title="Call again" kind="outline" onPress={() => void call.begin()} />
            ) : null}
        </ScrollView>
    );
}

/** The face made small, while something is on screen. */
function Head({ call, podId }: { call: VoiceCall; podId: string }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingLeft: 12, paddingRight: 20, paddingBottom: 6 }}>
            <VoiceFace podId={podId} icon={call.icon} size={36} level={call.level} on />
            <View style={{ flex: 1, minWidth: 0 }}>
                <Text accessibilityRole="header" size={16} weight="medium" numberOfLines={1}>{call.words.title}</Text>
                {call.words.line ? (
                    <Text accessibilityLiveRegion="polite" size={13.5} tone={call.words.tone === "bad" ? color.amber : color.ink2} numberOfLines={1}>{call.words.line}</Text>
                ) : null}
            </View>
        </View>
    );
}

/** One thing on screen at a time, the newest the moment it arrives. Every
 *  one stays mounted and all but one are hidden, so paging back does not
 *  reload a widget already looked at. Each is drawn from the chat that put
 *  it there: the Lemma and each piece of work it handed on may draw. What is
 *  on screen is told to the call, so the voice knows what the person sees. */
function Deck({ items, podId, onShow, onCompose }: { items: CanvasItem[]; podId: string; onShow: (item: CanvasItem) => void; onCompose: (text: string) => void }) {
    const [showing, setShowing] = useState(0);
    const newest = Math.max(0, items.length - 1);
    useEffect(() => { setShowing(newest); }, [newest]);
    const active = shownIndex(showing, items.length);
    const onScreen = items[active];
    const shown = useRef<string | null>(null);
    useEffect(() => {
        if (!onScreen || shown.current === onScreen.id) return;
        shown.current = onScreen.id;
        onShow(onScreen);
    }, [onScreen, onShow]);
    return (
        <View style={{ flex: 1, borderTopWidth: 1, borderTopColor: color.line }}>
            <View style={{ flex: 1 }}>
                {items.map((item, index) => (
                    <ScrollView
                        key={item.id}
                        style={{ display: index === active ? "flex" : "none", flex: 1 }}
                        contentContainerStyle={{ padding: 16 }}
                        accessibilityElementsHidden={index !== active}
                        importantForAccessibility={index === active ? "auto" : "no-hide-descendants"}
                    >
                        <ResourceCard resource={item.resource} podId={podId} conversationId={item.conversationId} toolCallId={item.id} onCompose={onCompose} />
                    </ScrollView>
                ))}
            </View>
            {items.length > 1 ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, borderTopWidth: 1, borderTopColor: color.line }}>
                    <Press label="Previous" disabled={active === 0} onPress={() => setShowing(active - 1)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center", opacity: active === 0 ? 0.35 : 1 }}>
                        <Icon name="back" size={20} />
                    </Press>
                    <View style={{ flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 }}>
                        <Text size={14} numberOfLines={1} style={{ flexShrink: 1 }}>{onScreen?.label}</Text>
                        <Num size={13} tone={color.ink3}>{deckWhere(active, items.length)}</Num>
                    </View>
                    <Press label="Next" disabled={active === newest} onPress={() => setShowing(active + 1)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center", opacity: active === newest ? 0.35 : 1 }}>
                        <Icon name="forward" size={20} />
                    </Press>
                </View>
            ) : null}
        </View>
    );
}

/** One line, the one being said: a call is spoken, and the chat keeps the
 *  record. The finished line stays up until the next begins. */
function Caption({ caption }: { caption: { who: string; text: string } | null }) {
    return (
        <View accessibilityLiveRegion="polite" accessibilityLabel={caption ? `${caption.who}: ${caption.text}` : "Captions"} style={{ minHeight: 76, justifyContent: "flex-end", paddingHorizontal: 20, paddingTop: 8, paddingBottom: 6, borderTopWidth: 1, borderTopColor: color.line }}>
            {caption ? (
                <>
                    <Text size={12.5} tone={color.ink3}>{caption.who}</Text>
                    <Text size={17} numberOfLines={2} style={{ lineHeight: 23 }}>{caption.text}</Text>
                </>
            ) : null}
        </View>
    );
}
