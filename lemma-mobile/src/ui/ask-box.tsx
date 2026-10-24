import { useState } from "react";
import { Alert, Keyboard, ScrollView, TextInput, View } from "react-native";
import { Image } from "expo-image";
import Animated, { FadeOutDown, ReduceMotion, SlideInDown } from "react-native-reanimated";
import { useRouter } from "expo-router";
import type { PickedFile } from "../data/io";
import type { Lemma } from "../data/types";
import { moreRoutes } from "../areas/more/routes";
import { NOTHING_TYPED, offered } from "../thread/attachments";
import { LemmaFace } from "./face";
import { AttachToggle, AttachTray } from "./files-in-chat";
import { Icon } from "./icons";
import { useIdentity } from "./identity";
import { spring } from "./motion";
import { Press } from "./press";
import { Text } from "./text";
import { Sheet } from "./sheet";
import { color, font, TOUCH } from "./tokens";

/** The box a person asks in. On Home and a new chat it carries a "To
 *  Support ▾" chip that picks which Lemma hears it; on a Lemma's own page it
 *  is fixed to that Lemma and is one row. Sending hands the words up; the
 *  screen creates the chat and opens it. */

export function notYet(what: string): void {
    Alert.alert("Not on the phone yet", `${what} works on the web today. It comes to the app next.`);
}

/** Send, filled in `tone` (a Lemma's colour in its chat) once there is
 *  something to send. */
export function SendButton({ ready, onPress, label = "Send", tone = color.ink, onTone = color.onInk }: { ready: boolean; onPress: () => void; label?: string; tone?: string; onTone?: string }) {
    return (
        <Press
            label={label}
            onPress={onPress}
            disabled={!ready}
            accessibilityState={{ disabled: !ready }}
            hitSlop={8}
            style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: ready ? tone : color.idle, alignItems: "center", justifyContent: "center" }}
        >
            <Icon name="send" size={16} width={2} tone={ready ? onTone : color.idleInk} />
        </Press>
    );
}

/** A voice call with the Lemma the box is for (area G, `moreRoutes.call`);
 *  nothing where the box is for no Lemma yet. */
export function MicButton({ onPress, label = "Start a voice call" }: { onPress?: (() => void) | null; label?: string }) {
    if (!onPress) return null;
    return (
        <Press label={label} onPress={onPress} style={{ width: 36, height: 36, alignItems: "center", justifyContent: "center" }}>
            <Icon name="mic" size={20} tone={color.ink2} />
        </Press>
    );
}

export function LemmaChip({ lemma, onPress }: { lemma: Lemma; onPress: () => void }) {
    return (
        <Press
            label={`To ${lemma.name}. Change who you ask`}
            onPress={onPress}
            hitSlop={8}
            style={{ flexDirection: "row", alignItems: "center", gap: 6, height: 30, paddingLeft: 6, paddingRight: 10, borderRadius: 999, backgroundColor: color.wash }}
        >
            <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={18} />
            <Text size={13.5}>To {lemma.name}</Text>
            <Icon name="down" size={12} width={2} tone={color.ink2} />
        </Press>
    );
}

/** Which Lemma to ask, as a sheet from the bottom. */
export function LemmaPicker({ lemmas, chosen, open, onClose, onPick }: {
    lemmas: readonly Lemma[];
    chosen: string | null;
    open: boolean;
    onClose: () => void;
    onPick: (id: string) => void;
}) {
    return (
        <Sheet open={open} onClose={onClose} title="Ask">
                    <ScrollView>
                        {lemmas.map((lemma) => (
                            <Press
                                key={lemma.id}
                                label={lemma.name}
                                accessibilityState={{ selected: lemma.id === chosen }}
                                onPress={() => {
                                    onPick(lemma.id);
                                    onClose();
                                }}
                                style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 20 }}
                            >
                                <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={30} />
                                <Text size={16} style={{ flex: 1 }}>{lemma.name}</Text>
                                {lemma.id === chosen ? <Icon name="check" size={18} width={2} tone={color.ink} /> : null}
                            </Press>
                        ))}
                    </ScrollView>
        </Sheet>
    );
}

/** The two-row box with the picker: words on top, the chip under them.
 *  With `attach`, the + opens the attach tray above the box (the camera,
 *  the latest photos, files), and what is picked goes with the words to the
 *  chat that is opened for them. Send is the chosen Lemma's colour. */
export function AskBox({ lemmas, podId, onPick, onSend, placeholder = "Ask anything, or say what to do", attach = false, autoFocus = false }: {
    lemmas: readonly Lemma[];
    podId: string | null;
    onPick: (id: string) => void;
    onSend: (podId: string, text: string, files: PickedFile[]) => void;
    placeholder?: string;
    attach?: boolean;
    autoFocus?: boolean;
}) {
    const router = useRouter();
    const [text, setText] = useState("");
    const [picking, setPicking] = useState(false);
    const [tray, setTray] = useState(false);
    const [files, setFiles] = useState<PickedFile[]>([]);
    const lemma = lemmas.find((one) => one.id === podId) ?? lemmas[0] ?? null;
    const identity = useIdentity(lemma?.id ?? "");
    const ready = Boolean((text.trim() || files.length > 0) && lemma);
    const send = () => {
        if (!ready || !lemma) return;
        onSend(lemma.id, text.trim() || NOTHING_TYPED, files);
        setText("");
        setFiles([]);
        setTray(false);
    };
    const take = (picked: PickedFile[]) => {
        const { take: kept, refused } = offered(picked);
        if (refused) Alert.alert("Not attached", refused);
        if (kept.length > 0) setFiles((was) => [...was, ...kept]);
    };
    const remove = (index: number) => setFiles((was) => was.filter((_, at) => at !== index));
    return (
        <View style={{ gap: 8 }}>
            {tray && attach ? (
                <Animated.View entering={SlideInDown.springify().damping(spring.damping ?? 20).stiffness(spring.stiffness ?? 190).reduceMotion(ReduceMotion.System)} exiting={FadeOutDown.duration(200).reduceMotion(ReduceMotion.System)}>
                    <AttachTray
                        chosen={new Set(files.map((file) => file.uri))}
                        onPicked={take}
                        onUnpicked={(uri) => setFiles((was) => was.filter((file) => file.uri !== uri))}
                        onRefused={(why) => Alert.alert("Not attached", why)}
                        onDone={() => setTray(false)}
                        tone={identity.ink}
                        onTone={identity.onInk}
                    />
                </Animated.View>
            ) : null}
            <View style={{ borderWidth: 1, borderColor: color.line2, borderRadius: 22, paddingTop: 12, paddingBottom: 8, paddingLeft: 14, paddingRight: 12, gap: 8, backgroundColor: color.paper }}>
                {files.length > 0 ? (
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, alignItems: "center" }} keyboardShouldPersistTaps="handled">
                        {files.map((file, index) => (file.type.startsWith("image/") ? (
                            <View key={file.uri + index} accessibilityLabel={file.name} style={{ width: 52, height: 52 }}>
                                <Image source={{ uri: file.uri }} style={{ width: 48, height: 48, borderRadius: 10, marginTop: 4, backgroundColor: color.wash }} contentFit="cover" />
                                <Press label={"Remove " + file.name} onPress={() => remove(index)} hitSlop={8} style={{ position: "absolute", right: 0, top: 0, width: 20, height: 20, borderRadius: 10, backgroundColor: color.ink, alignItems: "center", justifyContent: "center" }}>
                                    <Icon name="close" size={10} width={2.4} tone={color.onInk} />
                                </Press>
                            </View>
                        ) : (
                            <View key={file.uri + index} accessibilityLabel={file.name} style={{ flexDirection: "row", alignItems: "center", gap: 6, maxWidth: 240, height: 30, paddingLeft: 10, paddingRight: 2, borderRadius: 999, borderWidth: 1, borderColor: color.line2 }}>
                                <Text size={13} numberOfLines={1} style={{ flexShrink: 1 }}>{file.name}</Text>
                                <Press label={"Remove " + file.name} onPress={() => remove(index)} style={{ width: 26, height: 26, alignItems: "center", justifyContent: "center" }}>
                                    <Icon name="close" size={12} width={2} tone={color.ink2} />
                                </Press>
                            </View>
                        )))}
                    </ScrollView>
                ) : null}
                <TextInput
                    accessibilityLabel={lemma ? `Ask ${lemma.name}` : "Ask a Lemma"}
                    value={text}
                    onChangeText={setText}
                    placeholder={placeholder}
                    placeholderTextColor={color.ink3}
                    multiline
                    autoFocus={autoFocus}
                    onFocus={() => setTray(false)}
                    style={{ fontFamily: font.regular, fontSize: 16, color: color.ink, padding: 0, maxHeight: 120, minHeight: 22 }}
                />
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                    {lemma ? <LemmaChip lemma={lemma} onPress={() => setPicking(true)} /> : null}
                    <View style={{ flex: 1 }} />
                    {attach ? <AttachToggle open={tray} onPress={() => { if (!tray) Keyboard.dismiss(); setTray((was) => !was); }} /> : <MicButton label={lemma ? "Call " + lemma.name : undefined} onPress={lemma ? () => router.push(moreRoutes.call(lemma.id) as never) : null} />}
                    <SendButton ready={ready} onPress={send} tone={identity.ink} onTone={identity.onInk} />
                </View>
                <LemmaPicker lemmas={lemmas} chosen={lemma?.id ?? null} open={picking} onClose={() => setPicking(false)} onPick={onPick} />
            </View>
        </View>
    );
}

/** One row, fixed to a Lemma: a Lemma's own page. */
export function FixedAskBox({ name, onSend, podId }: { name: string; onSend: (text: string) => void; podId?: string | null }) {
    const router = useRouter();
    const [text, setText] = useState("");
    const ready = Boolean(text.trim());
    const send = () => {
        if (!ready) return;
        onSend(text.trim());
        setText("");
    };
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 1, borderColor: color.line2, borderRadius: 22, paddingVertical: 6, paddingRight: 6, paddingLeft: 16, minHeight: TOUCH + 4, backgroundColor: color.paper }}>
            <TextInput
                accessibilityLabel={`Ask ${name}`}
                value={text}
                onChangeText={setText}
                placeholder={`Ask ${name}`}
                placeholderTextColor={color.ink3}
                onSubmitEditing={send}
                returnKeyType="send"
                style={{ flex: 1, minWidth: 0, fontFamily: font.regular, fontSize: 16, color: color.ink, padding: 0 }}
            />
            <MicButton label={"Call " + name} onPress={podId ? () => router.push(moreRoutes.call(podId) as never) : null} />
            <SendButton ready={ready} onPress={send} />
        </View>
    );
}
