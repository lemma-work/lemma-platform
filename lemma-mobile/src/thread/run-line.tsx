import { useState } from "react";
import { ScrollView, View, useWindowDimensions } from "react-native";
import Animated, { FadeIn, FadeInDown, FadeOut, FadeOutUp, LayoutAnimationConfig, ReduceMotion } from "react-native-reanimated";
import { Drawer } from "../ui/drawer";
import { Icon } from "../ui/icons";
import { MOVE_MS, settle } from "../ui/motion";
import { Press } from "../ui/press";
import { Num, Text } from "../ui/text";
import { color, font } from "../ui/tokens";
import { BreathingDot } from "./breathing";
import { ToolCardView } from "./cards";
import { useLook } from "./look";
import { stepDetail, tickerLine, tookOf, workedLine } from "./steps";
import type { Note } from "@web/thread/turns";

const curve = settle.easing!;

/** A run's work, as one line under the newest thing it said: while it goes,
 *  the step it is on beside a breathing dot in the Lemma's colour, each new
 *  step rising into the line; when it ends, the line folds into "Worked for
 *  1m 6s · 5 steps ›", which opens the steps in a drawer. */
export function RunLine({ notes, live, writing, span, name, podId, conversationId }: {
    notes: Note[];
    live: boolean;
    /** The reply's own words are coming in. */
    writing: boolean;
    span: string;
    name: string;
    podId: string;
    conversationId: string | null;
}) {
    const look = useLook();
    const [open, setOpen] = useState(false);
    if (!live && notes.length === 0) return null;
    const line = tickerLine(notes, writing);
    const worked = workedLine(span, notes.length);
    return (
        /* Drawn as it is when the chat opens; only a change afterwards moves. */
        <LayoutAnimationConfig skipEntering>
            {live ? (
                <Animated.View key="live" exiting={FadeOut.duration(160)} accessibilityLiveRegion="polite" accessibilityLabel={`${name} is working: ${line}`} style={{ flexDirection: "row", alignItems: "center", gap: 9, minHeight: 26, alignSelf: "stretch" }}>
                    <BreathingDot tone={look.ink} />
                    <View style={{ flex: 1, height: 20, overflow: "hidden" }}>
                        <Animated.Text
                            key={line}
                            entering={FadeInDown.duration(MOVE_MS).easing(curve).reduceMotion(ReduceMotion.System)}
                            exiting={FadeOutUp.duration(260).reduceMotion(ReduceMotion.System)}
                            numberOfLines={1}
                            style={{ position: "absolute", left: 0, right: 0, top: 0, fontFamily: font.regular, fontSize: 13.5, lineHeight: 20, color: color.ink2 }}
                        >
                            {line}
                        </Animated.Text>
                    </View>
                </Animated.View>
            ) : (
                <Animated.View key="done" entering={FadeIn.duration(MOVE_MS).easing(curve).reduceMotion(ReduceMotion.System)}>
                    <Press
                        label={`${worked}. Show how ${name} got there`}
                        onPress={() => setOpen(true)}
                        hitSlop={8}
                        style={{ alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 5, minHeight: 28 }}
                    >
                        <Text size={13} tone={color.ink3}>{worked}</Text>
                        <Icon name="forward" size={11} width={2} tone={color.ink3} />
                    </Press>
                </Animated.View>
            )}
            {open ? <StepsDrawer notes={notes} worked={worked} name={name} podId={podId} conversationId={conversationId} onClose={() => setOpen(false)} /> : null}
        </LayoutAnimationConfig>
    );
}

/** "How Support got there": the steps in order, each with how long it
 *  took, the ones the web reads drawn as their cards. */
function StepsDrawer({ notes, worked, name, podId, conversationId, onClose }: {
    notes: Note[];
    worked: string;
    name: string;
    podId: string;
    conversationId: string | null;
    onClose: () => void;
}) {
    const { height } = useWindowDimensions();
    const look = useLook();
    const [open, setOpen] = useState(true);
    const close = () => {
        setOpen(false);
        setTimeout(onClose, 300);
    };
    return (
        <Drawer open={open} onClose={close} height={Math.round(height * 0.78)}>
            <View style={{ paddingHorizontal: 20, paddingTop: 6, paddingBottom: 10, gap: 2 }}>
                <Text accessibilityRole="header" size={18} weight="medium">How {name} got there</Text>
                <Text size={13} tone={color.ink3}>{worked}</Text>
            </View>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 40 }}>
                {notes.map((note, index) => {
                    const took = tookOf(look.timeOf(note.toolCallId));
                    const detail = stepDetail(note);
                    return (
                        <View key={index} style={{ flexDirection: "row", gap: 12, paddingLeft: note.nested ? 18 : 0 }}>
                            <View style={{ width: 14, alignItems: "center" }}>
                                <View style={{ width: 7, height: 7, borderRadius: 3.5, marginTop: 15, backgroundColor: note.kind === "thought" ? color.line2 : look.ink }} />
                                {index < notes.length - 1 ? <View style={{ flex: 1, width: 1, backgroundColor: color.line2, marginTop: 4 }} /> : null}
                            </View>
                            <View style={{ flex: 1, minWidth: 0, paddingVertical: 8, gap: 6 }}>
                                <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
                                    <Text size={14.5} tone={color.ink} numberOfLines={1} style={{ flex: 1 }}>{note.kind === "thought" ? "Thought" : note.label}</Text>
                                    {took ? <Num size={12} tone={color.ink3}>{took}</Num> : null}
                                </View>
                                {note.card ? (
                                    <ToolCardView card={note.card} podId={podId} conversationId={conversationId} toolCallId={note.toolCallId} />
                                ) : detail ? (
                                    <Text size={13} tone={note.said ? color.ink2 : color.ink3} numberOfLines={note.kind === "thought" ? 6 : 3} style={{ lineHeight: 18 }}>{detail}</Text>
                                ) : null}
                            </View>
                        </View>
                    );
                })}
            </ScrollView>
        </Drawer>
    );
}
