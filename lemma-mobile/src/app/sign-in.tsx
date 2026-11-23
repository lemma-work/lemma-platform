import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Modal, ScrollView, useWindowDimensions, View } from "react-native";
import Animated, { FadeInDown } from "react-native-reanimated";
import { SafeAreaView } from "react-native-safe-area-context";
import Svg, { Path } from "react-native-svg";
import { AppleAuthenticationButton, AppleAuthenticationButtonStyle, AppleAuthenticationButtonType } from "expo-apple-authentication";
import * as Crypto from "expo-crypto";
import * as WebBrowser from "expo-web-browser";
import { useSession } from "../session/session";
import type { Server } from "../session/server";
import { offersApple, signInWithApple } from "../session/apple";
import { dropHandoff, offersGoogle, pendingHandoff, startHandoff, type Handoff } from "../session/handoff";
import { RETURN_URL } from "../session/handoff-link";
import { SignInWebView } from "../session/web-session";
import type { Tokens } from "../session/auth";
import { LemmaFace } from "../ui/face";
import { Icon } from "../ui/icons";
import { Button, Press } from "../ui/press";
import { ServerPicker } from "../ui/server-picker";
import { Num, Text } from "../ui/text";
import { useTheme } from "../ui/theme";
import { color, TOUCH } from "../ui/tokens";

/** Signed out. Sign in with Apple's own sheet on an iPhone (`apple.ts`), on
 *  the web's own page in a WebView, or in the phone's browser for Google,
 *  which refuses WebViews (`handoff.ts`); or look around with sample data
 *  first. Which Lemma it signs in to can be changed, for a dev stack on this
 *  computer. */
export default function SignIn() {
    const { server, setServer, completeSignIn, chooseSample } = useSession();
    const { scheme } = useTheme();
    const [stage, setStage] = useState<"start" | "web" | "browser">("start");
    const [handoff, setHandoff] = useState<Handoff | null>(null);
    const [starting, setStarting] = useState(false);
    const [apple, setApple] = useState(false);
    const [google, setGoogle] = useState(false);
    const [who, setWho] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [choosing, setChoosing] = useState(false);
    const nonce = useMemo(() => Crypto.randomUUID(), [stage, handoff]);

    useEffect(() => {
        let live = true;
        setApple(false);
        setGoogle(false);
        void offersApple(server).then((offered) => { if (live) setApple(offered); });
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

    /* Apple's sheet, then the server; nothing changes if the person closes
       the sheet. One at a time with the browser's way in, above. */
    const withApple = useCallback(async () => {
        if (opening.current) return;
        opening.current = true;
        setProblem(null);
        try {
            const tokens = await signInWithApple(server, (email) => setWho(email ?? "your account"));
            if (tokens) finish(tokens);
            else setWho(null);
        } catch (error) {
            fail(error instanceof Error ? error.message : "Signing in with Apple did not work. Try again.");
        } finally {
            opening.current = false;
        }
    }, [server, finish, fail]);

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

    const providers = apple || google;
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            {/* The web's door (`lemma-frontend/src/auth/frame.tsx`), in one
                column: who this is for on top -- the cast, with the one
                sentence that says what a Lemma is -- and the ways in under
                it, providers first, then email after an "or". */}
            <View style={{ flex: 1, justifyContent: "center", paddingHorizontal: 24, gap: 10 }}>
                <Cast />
                <Text accessibilityRole="header" size={26} weight="medium" style={{ letterSpacing: -0.4, lineHeight: 32, textAlign: "center", marginTop: 34, marginBottom: 10 }}>Sign in or make your account</Text>
                {problem ? <Text size={14.5} tone={color.amber} style={{ textAlign: "center", marginBottom: 4 }}>{problem}</Text> : null}
                {/* Apple's own button, in the only two looks Apple allows:
                    black on a light screen, white on a dark one. Google and
                    email sit under it as outlines, as every way in does on
                    the web, so there is one filled button. */}
                {apple ? (
                    <AppleAuthenticationButton
                        buttonType={AppleAuthenticationButtonType.CONTINUE}
                        buttonStyle={scheme === "dark" ? AppleAuthenticationButtonStyle.WHITE : AppleAuthenticationButtonStyle.BLACK}
                        cornerRadius={12}
                        style={{ height: WAY_IN_HEIGHT }}
                        onPress={() => void withApple()}
                    />
                ) : null}
                {google ? <WayIn title="Continue with Google" mark={<GoogleMark />} filled={!apple} busy={starting} onPress={() => void inBrowser()} /> : null}
                {providers ? <Or /> : null}
                <WayIn title="Continue with email" mark={<Icon name="mail" size={19} tone={providers ? color.ink : color.onInk} />} filled={!providers} onPress={() => { setProblem(null); setStage("web"); }} />
            </View>
            <View style={{ alignItems: "center", paddingBottom: 6 }}>
                <Press label="Look around with sample data" onPress={() => void chooseSample()} style={{ minHeight: TOUCH, justifyContent: "center", paddingHorizontal: 12 }}>
                    <Text size={15} tone={color.ink2}>Look around with sample data</Text>
                </Press>
                <Press label={`Signing in to ${server.label}. Change`} onPress={() => setChoosing(true)} style={{ minHeight: 32, justifyContent: "center", paddingHorizontal: 8 }}>
                    <Text size={13} tone={color.ink3}>Signing in to {server.label} · <Text size={13} tone={color.ink3} style={{ textDecorationLine: "underline" }}>Change</Text></Text>
                </Press>
            </View>
            <ServerSheet open={choosing} current={server} onClose={() => setChoosing(false)} onPick={(next) => { setChoosing(false); void setServer(next); }} />
            {who ? <Signing who={who} /> : null}
        </SafeAreaView>
    );
}

/** Three of the cast standing on their tints, as the web's door draws them
 *  (`.auth-cast`): the middle one larger and higher, one each side lower
 *  down. They stand up once as the screen opens; nothing moves after.
 *  Spots are fractions of the width, and the box is only as tall as the
 *  tiles, so the sentence under them sits with them. */
const CAST_FACES = ["bloom", "spark", "cube"] as const;
const CAST_SPOTS = [
    { left: 0.31, top: 0, width: 0.38, z: 2 },
    { left: 0.06, top: 0.19, width: 0.28, z: 1 },
    { left: 0.67, top: 0.21, width: 0.27, z: 1 },
] as const;
const CAST_HEIGHT = 0.48;

function Cast() {
    const screen = useWindowDimensions();
    /* The column's width, and less on a short phone so the ways in never
       scroll off. */
    const width = Math.max(180, Math.min(360, screen.width - 48, (screen.height - 520) / CAST_HEIGHT));
    const height = width * CAST_HEIGHT;
    return (
        <View style={{ alignItems: "center", gap: 22 }}>
            <View style={{ width, height }} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
                {CAST_FACES.map((face, index) => {
                    const spot = CAST_SPOTS[index];
                    const size = Math.round(width * spot.width);
                    return (
                        <Animated.View
                            key={face}
                            entering={FadeInDown.delay(index * 90).duration(600)}
                            style={{
                                position: "absolute",
                                left: width * spot.left,
                                top: width * spot.top,
                                zIndex: spot.z,
                                borderRadius: Math.round(size * 0.3),
                                shadowColor: "#000",
                                shadowOpacity: 0.14,
                                shadowRadius: 14,
                                shadowOffset: { width: 0, height: 8 },
                                elevation: 4,
                            }}
                        >
                            <LemmaFace id={face} icon={`/teammates/cast/${face}.webp`} size={size} />
                        </Animated.View>
                    );
                })}
            </View>
            <Text size={15.5} tone={color.ink2} style={{ textAlign: "center", lineHeight: 22, maxWidth: 340 }}>Each Lemma looks after one part of your work.</Text>
        </View>
    );
}

/** Every way in is this tall. Apple's button sizes its own title from its
 *  height; at 48 its words come out close to the size of the others'. */
const WAY_IN_HEIGHT = 48;

/** One way in: the web's large button, with the mark of where it goes. */
function WayIn({ title, mark, filled, busy = false, onPress }: { title: string; mark: ReactNode; filled: boolean; busy?: boolean; onPress: () => void }) {
    return (
        <Press
            label={title}
            onPress={onPress}
            disabled={busy}
            accessibilityState={{ busy }}
            style={{
                height: WAY_IN_HEIGHT,
                borderRadius: 12,
                borderWidth: filled ? 0 : 1,
                borderColor: color.line2,
                backgroundColor: filled ? color.ink : "transparent",
                flexDirection: "row",
                alignItems: "center",
                justifyContent: "center",
                gap: 10,
            }}
        >
            {busy ? <ActivityIndicator color={filled ? color.onInk : color.ink} /> : (
                <>
                    {mark}
                    <Text size={17} weight="medium" tone={filled ? color.onInk : color.ink}>{title}</Text>
                </>
            )}
        </Press>
    );
}

/** The web's "or" between the providers and email: a rule with the word in it. */
function Or() {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 12, marginVertical: 2 }}>
            <View style={{ flex: 1, height: 1, backgroundColor: color.line2 }} />
            <Text size={13} tone={color.ink3}>or</Text>
            <View style={{ flex: 1, height: 1, backgroundColor: color.line2 }} />
        </View>
    );
}

/** Google's mark, as the web's sign-in draws it (`auth/marks.tsx`). */
function GoogleMark({ size = 18 }: { size?: number }) {
    return (
        <Svg width={size} height={size} viewBox="0 0 48 48" accessibilityElementsHidden importantForAccessibility="no">
            <Path fill="#4285F4" d="M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z" />
            <Path fill="#34A853" d="M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z" />
            <Path fill="#FBBC05" d="M11.69 28.18C11.25 26.86 11 25.45 11 24s.25-2.86.69-4.18v-5.7H4.34C2.85 17.09 2 20.45 2 24c0 3.55.85 6.91 2.34 9.88l7.35-5.7z" />
            <Path fill="#EA4335" d="M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z" />
        </Svg>
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
