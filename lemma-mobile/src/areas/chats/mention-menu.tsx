import { useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import Animated, { interpolate, useAnimatedStyle, useSharedValue, withTiming } from "react-native-reanimated";
import { haptics } from "../../ui/haptics";
import { Icon } from "../../ui/icons";
import { settle } from "../../ui/motion";
import { Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { SaidBy } from "./bits";
import { stripOf, withoutQuery } from "./room";
import { insertMention, mentionAt, type Mentionable } from "@web/channels/mentions";

const FACE = 40;

/** The `@` of a box a message is written in -- the composer, and the box a
 *  message is changed in (the web's `useMentionPicker`, for a touch screen).
 *  `@` at the start or after a space slides up a strip of faces: the room's
 *  Lemmas as their characters, then its people as discs, narrowed by what
 *  follows the `@`.
 *  A person picked is written in as "@Name " and remembered beside the
 *  words; a Lemma picked, where the box asks Lemmas (`onAsk`), leaves the
 *  words alone and is handed over instead, to be asked by the whole box. */
export function useMentionMenu({ people, text, setText, onPicked, onAsk, lemma = null, frontId = null }: {
    people: readonly Mentionable[];
    text: string;
    setText: (text: string) => void;
    onPicked: (who: Mentionable) => void;
    /** A Lemma picked from the strip; without it, a Lemma is written in as a person is. */
    onAsk?: (who: Mentionable) => void;
    /** The Lemma whose agents are offered: they wear its face. */
    lemma?: { id: string; iconUrl: string | null } | null;
    frontId?: string | null;
}) {
    const [caret, setCaret] = useState(0);
    const [dismissed, setDismissed] = useState<number | null>(null);
    /* Set for one draw after a pick, so the caret lands after the name; a
       box held at a selection otherwise fights the person typing. */
    const [selection, setSelection] = useState<{ start: number; end: number } | undefined>(undefined);
    const at = mentionAt(text, Math.min(caret, text.length));
    const offered = at && dismissed !== at.start ? stripOf(people, at.query) : [];

    const land = (to: number) => {
        setCaret(to);
        setSelection({ start: to, end: to });
        setTimeout(() => setSelection(undefined), 60);
    };

    const pick = (who: Mentionable) => {
        if (!at) return;
        haptics.tick();
        if (who.kind === "AGENT" && onAsk) {
            const next = withoutQuery(text, at, caret);
            setText(next.text);
            onAsk(who);
            land(next.caret);
            return;
        }
        const next = insertMention(text, at, caret, who);
        setText(next.text);
        onPicked(who);
        land(next.caret);
    };

    /** Put an "@" where the caret is, after a space when it follows a word:
     *  the same as typing it. */
    const begin = () => {
        const before = text.slice(0, caret);
        const insert = before && !/\s$/.test(before) ? " @" : "@";
        const next = before + insert + text.slice(caret);
        setText(next);
        setDismissed(null);
        land(before.length + insert.length);
    };

    const menu = offered.length > 0 ? (
        <MentionStrip offered={offered} lemma={lemma} frontId={frontId} asks={Boolean(onAsk)} onPick={pick} onClose={() => at && setDismissed(at.start)} />
    ) : null;

    return {
        menu,
        begin,
        selection,
        onSelectionChange: (event: { nativeEvent: { selection: { start: number; end: number } } }) => setCaret(event.nativeEvent.selection.start),
        /* Where the caret is comes from the box's own selection events, which
           follow every keystroke; the two may land in either order, and the
           pair agrees again on the next draw. */
        onChangeText: (value: string) => {
            setText(value);
            setDismissed(null);
        },
    };
}

/** The strip itself, sliding up from the box when it opens. */
function MentionStrip({ offered, lemma, frontId, asks, onPick, onClose }: {
    offered: readonly Mentionable[];
    lemma: { id: string; iconUrl: string | null } | null;
    frontId: string | null;
    asks: boolean;
    onPick: (who: Mentionable) => void;
    onClose: () => void;
}) {
    const shown = useSharedValue(0);
    useEffect(() => {
        shown.value = withTiming(1, settle);
    }, [shown]);
    const rise = useAnimatedStyle(() => ({ opacity: interpolate(shown.value, [0, 0.6], [0, 1], "clamp"), transform: [{ translateY: interpolate(shown.value, [0, 1], [18, 0]) }] }));
    return (
        <Animated.View accessibilityRole="menu" accessibilityLabel="Name someone" style={[{ flexDirection: "row", alignItems: "center", borderBottomWidth: 1, borderBottomColor: color.line, paddingBottom: 6 }, rise]}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="always" contentContainerStyle={{ gap: 4, paddingHorizontal: 2 }} style={{ flex: 1 }}>
                {offered.map((who) => {
                    const lemmaHere = who.kind === "AGENT";
                    const label = lemmaHere && asks ? "Ask " + who.name + (who.inChannel === false ? ", this once" : "") : "Name " + who.name + (who.kind === "CONTACT" ? ", a contact" : "");
                    return (
                        <Press key={who.kind + who.id} role="button" label={label} onPress={() => onPick(who)} style={{ width: 64, alignItems: "center", gap: 4, paddingVertical: 4 }}>
                            <View style={{ opacity: lemmaHere && who.inChannel === false ? 0.75 : 1 }}>
                                <SaidBy person={{ kind: who.kind, id: who.id, name: who.name }} lemma={lemma} frontId={frontId} size={FACE} />
                            </View>
                            <Text size={12} tone={color.ink2} numberOfLines={1} style={{ maxWidth: 62 }}>{lemmaHere ? who.name : who.name.split(/\s+/)[0]}</Text>
                        </Press>
                    );
                })}
            </ScrollView>
            <Press label="Close the list" onPress={onClose} style={{ width: 36, height: 44, alignItems: "center", justifyContent: "center" }}>
                <Icon name="close" size={13} width={2} tone={color.ink3} />
            </Press>
        </Animated.View>
    );
}
