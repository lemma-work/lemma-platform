import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Modal, ScrollView, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Crypto from "expo-crypto";
import * as WebBrowser from "expo-web-browser";
import { useSession } from "../session/session";
import type { Server } from "../session/server";
import { dropHandoff, offersGoogle, pendingHandoff, startHandoff, type Handoff } from "../session/handoff";
import { RETURN_URL } from "../session/handoff-link";
import { SignInWebView } from "../session/web-session";
import type { Tokens } from "../session/auth";
import { LemmaMark } from "../ui/icons";
import { Button, Press } from "../ui/press";
import { ServerPicker } from "../ui/server-picker";
import { Num, Text } from "../ui/text";
import { color, TOUCH } from "../ui/tokens";

/** Signed out. Sign in on the web's own page, in a WebView, or in the
 *  phone's browser for Google, which refuses WebViews (`handoff.ts`); or
 *  look around with sample data first. Which Lemma it signs in to can be
 *  changed, for a dev stack on this computer. */
export default function SignIn() {
    const { server, setServer, completeSignIn, chooseSample } = useSession();
    const [stage, setStage] = useState<"start" | "web" | "browser">("start");
    const [handoff, setHandoff] = useState<Handoff | null>(null);
    const [starting, setStarting] = useState(false);
    const [google, setGoogle] = useState(false);
    const [who, setWho] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [choosing, setChoosing] = useState(false);
    const nonce = useMemo(() => Crypto.randomUUID(), [stage, handoff]);

    useEffect(() => {
        let live = true;
        setGoogle(false);
        void offersGoogle(server).then((offered) => { if (live) setGoogle(offered); });
        /* A sign-in left open in the browser, if Android stopped the app
           while the browser was in front. */
        void pendingHandoff(server).then((held) => {
            if (live && held) { setHandoff(held); setStage("browser"); }
        });
        return () => { live = false; };
    }, [server]);

    const finish = useCallback((tokens: Tokens) => {
        void dropHandoff();
        void completeSignIn(tokens);
    }, [completeSignIn]);

    const fail = useCallback((said: string) => {
        void dropHandoff();
        setProblem(said);
        setWho(null);
        setHandoff(null);
        setStage("start");
    }, []);

    /* One at a time: a redirect chain toward Google reaches `onLeave` more
       than once, and each call would open its own request. */
    const opening = useRef(false);
    const inBrowser = useCallback(async () => {
        if (opening.current) return;
        opening.current = true;
        setProblem(null);
        setStarting(true);
        try {
            /* The code first, and the browser only on the next tap, so the
               person has seen it before the page asks them to match it. */
            const opened = await startHandoff(server);
            setHandoff(opened);
            setWho(null);
            setStage("browser");
        } catch (error) {
            fail(error instanceof Error ? error.message : "Signing in in the browser could not start.");
        } finally {
            opening.current = false;
            setStarting(false);
        }
    }, [server, fail]);

    if (stage === "browser" && handoff) {
        return (
            <BrowserSignIn
                server={server}
                handoff={handoff}
                nonce={nonce}
                who={who}
                onSignedIn={(email) => setWho(email ?? "your account")}
                onSession={finish}
                onProblem={fail}
                onOpen={() => openBrowser(handoff)}
                onCancel={() => { void dropHandoff(); setHandoff(null); setWho(null); setStage("start"); }}
            />
        );
    }

    if (stage === "web") {
        return (
            <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
                    <Press label="Cancel" onPress={() => { setStage("start"); setWho(null); }} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center" }}>
                        <Text size={16}>Cancel</Text>
                    </Press>
                    <View style={{ flex: 1, alignItems: "center" }}>
                        <Text size={16} weight="medium">Sign in</Text>
                    </View>
                    <View style={{ width: 70 }} />
                </View>
                <SignInWebView
                    server={server}
                    nonce={nonce}
                    onSignedIn={(email) => setWho(email ?? "your account")}
                    onProblem={fail}
                    /* Google refuses this WebView, so its button carries on in
                       the browser instead. */
                    onLeave={() => void inBrowser()}
                    onSession={finish}
                />
                {who ? <Signing who={who} /> : null}
            </SafeAreaView>
        );
    }

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flex: 1, paddingHorizontal: 24, justifyContent: "center", gap: 18 }}>
                <LemmaMark size={40} />
                <Text accessibilityRole="header" size={30} weight="medium" style={{ letterSpacing: -0.6, lineHeight: 34 }}>What your Lemmas need from you, wherever you are.</Text>
                <Text size={16} tone={color.ink2} style={{ lineHeight: 23 }}>Answer them, ask them, and open their apps. Sign in with the account you use on the web.</Text>
                {problem ? <Text size={14.5} tone={color.amber}>{problem}</Text> : null}
            </View>
            <View style={{ paddingHorizontal: 24, paddingBottom: 20, gap: 12 }}>
                {google ? <Button title="Continue with Google" height={50} size={16} busy={starting} onPress={() => void inBrowser()} /> : null}
                <Button title={google ? "Sign in with email" : "Sign in"} kind={google ? "outline" : "primary"} height={50} size={16} onPress={() => { setProblem(null); setStage("web"); }} />
                <Button title="Look around with sample data" kind={google ? "quiet" : "outline"} height={50} size={16} onPress={() => void chooseSample()} />
                <Press label={`Signing in to ${server.label}. Change`} onPress={() => setChoosing(true)} style={{ alignSelf: "center", minHeight: TOUCH, justifyContent: "center", paddingHorizontal: 8 }}>
                    <Text size={13.5} tone={color.ink3}>Signing in to {server.label} · <Text size={13.5} tone={color.ink2} style={{ textDecorationLine: "underline" }}>Change</Text></Text>
                </Press>
            </View>
            <ServerSheet open={choosing} current={server} onClose={() => setChoosing(false)} onPick={(next) => { setChoosing(false); void setServer(next); }} />
        </SafeAreaView>
    );
}

/** The page sends the person back to `RETURN_URL`. On iOS the auth session
 *  catches that link and closes itself. On Android the Custom Tab opens in
 *  this app's task (`createTask: false`), so the link, which reopens the app,
 *  closes it too. Either way the result is not read here: the hidden WebView
 *  takes up the session whichever way the person comes back. */
async function openBrowser(handoff: Handoff): Promise<void> {
    try {
        await WebBrowser.openAuthSessionAsync(handoff.url, RETURN_URL, { createTask: false, showTitle: true });
    } catch {
        /* Already open, or no browser to open: the screen still offers it again. */
    }
}

function Signing({ who }: { who: string }) {
    return (
        <View style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0, backgroundColor: color.veil, alignItems: "center", justifyContent: "center", gap: 14 }}>
            <ActivityIndicator color={color.ink2} />
            <Text size={15.5} tone={color.ink2}>Signing in as {who}</Text>
        </View>
    );
}

/** Waiting on the browser. The page there shows the same code and asks the
 *  person to confirm it; the hidden WebView below takes up the session as
 *  soon as they do, whether the app came back by itself or they switched
 *  back by hand. */
function BrowserSignIn({ server, handoff, nonce, who, onSignedIn, onSession, onProblem, onOpen, onCancel }: {
    server: Server;
    handoff: Handoff;
    nonce: string;
    who: string | null;
    onSignedIn: (email: string | null) => void;
    onSession: (tokens: Tokens) => void;
    onProblem: (said: string) => void;
    onOpen: () => Promise<void>;
    onCancel: () => void;
}) {
    const [opened, setOpened] = useState(false);
    const open = () => { setOpened(true); void onOpen(); };
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 8 }}>
                <Press label="Cancel" onPress={onCancel} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center" }}>
                    <Text size={16}>Cancel</Text>
                </Press>
            </View>
            <View style={{ flex: 1, paddingHorizontal: 24, justifyContent: "center", gap: 16 }}>
                <Text accessibilityRole="header" size={26} weight="medium" style={{ letterSpacing: -0.4, lineHeight: 31 }}>Continue in your browser</Text>
                <Text size={16} tone={color.ink2} style={{ lineHeight: 23 }}>Sign in there with Google, or any other way. The page then shows a code: check it is this one, and confirm.</Text>
                <Num weight="medium" size={34} tone={color.ink} style={{ letterSpacing: 3, marginVertical: 6 }} accessibilityLabel={"Code " + handoff.code.split("").join(" ")}>{handoff.code}</Num>
            </View>
            <View style={{ paddingHorizontal: 24, paddingBottom: 20 }}>
                <Button title={opened ? "Open the browser again" : "Open the browser"} kind={opened ? "outline" : "primary"} height={50} size={16} onPress={open} />
            </View>
            <View style={{ position: "absolute", width: 1, height: 1, opacity: 0 }} pointerEvents="none">
                <SignInWebView
                    server={server}
                    nonce={nonce}
                    claim={{ requestId: handoff.requestId, verifier: handoff.verifier, expiresAt: handoff.expiresAt }}
                    onSignedIn={onSignedIn}
                    onSession={onSession}
                    onProblem={onProblem}
                    onLeave={() => undefined}
                />
            </View>
            {who ? <Signing who={who} /> : null}
        </SafeAreaView>
    );
}

function ServerSheet({ open, current, onClose, onPick }: { open: boolean; current: Server; onClose: () => void; onPick: (server: Server) => void }) {
    return (
        <Modal visible={open} animationType="slide" onRequestClose={onClose}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
                    <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 10 }}>
                        <Press label="Cancel" onPress={onClose} style={{ height: TOUCH, paddingHorizontal: 10, justifyContent: "center" }}>
                            <Text size={16}>Cancel</Text>
                        </Press>
                    </View>
                    <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 24, gap: 14 }} keyboardShouldPersistTaps="handled">
                        <Text accessibilityRole="header" size={24} weight="medium">Which Lemma</Text>
                        <ServerPicker current={current} onPick={onPick} />
                    </ScrollView>
                </SafeAreaView>
            </KeyboardAvoidingView>
        </Modal>
    );
}
