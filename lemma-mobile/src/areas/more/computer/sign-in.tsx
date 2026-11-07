import { useState, type ReactNode } from "react";
import { ActivityIndicator, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useIsFocused, useRouter } from "expo-router";
import { Glyph } from "../../../thread/glyphs";
import { BackLink } from "../../../ui/chrome";
import { Button, Press } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { problemOf } from "../../admin/words";
import { useAnswerSignIn, useComputerSource, useCurrentPage, useOpenInBrowser, useSignIn } from "./hooks";
import { glassTokens, LiveScreen } from "./live-screen";
import { opensInBrowser, outcomeWords, reasonLine, SAMPLE_SCREEN, SIGN_IN_FINE, SIGN_IN_NOT_YOURS, signInTrouble } from "./model";
import type { LiveState } from "@web/computer/live";
import { whereabouts } from "@web/computer/sign-in";

/** A site waiting on a person to sign in (the web's `sign-in-pane.tsx`):
 *  the computer's own browser, drivable, pointed at the site, with the two
 *  answers that let the paused run carry on.
 *
 *  The answers are the part that must not be missing: nothing notices a
 *  finished login by itself, so a screen that showed the browser without
 *  them would leave the Lemma waiting for ever. They sit at the foot,
 *  always. */
export function SignInScreen({ conversationId, toolCallId }: { conversationId: string; toolCallId: string }) {
    const router = useRouter();
    const source = useComputerSource();
    const focused = useIsFocused();
    const request = useSignIn(conversationId, toolCallId);
    const answer = useAnswerSignIn(conversationId, toolCallId);
    const tab = useOpenInBrowser();
    const [picture, setPicture] = useState<LiveState>("connecting");
    /* Bumped to open the socket again, which is what re-steers the browser
       to the site: the ordinary case is an answer hours late, after the
       browser it was aimed at was retired. */
    const [reopen, setReopen] = useState(0);
    const origin = request.data?.origin ?? null;
    const page = useCurrentPage(origin, source.machine && picture === "live" && !answer.data);
    const glass = glassTokens();

    const frame = (children: ReactNode) => (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            {children}
        </SafeAreaView>
    );
    const back = (
        <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6 }}>
            <BackLink />
        </View>
    );

    if (request.isPending) {
        return frame(
            <>
                {back}
                <View style={{ flexDirection: "row", alignItems: "center", gap: 10, padding: 20 }}>
                    <ActivityIndicator color={color.ink3} />
                    <Text size={15} tone={color.ink2}>Asking what this is for…</Text>
                </View>
            </>,
        );
    }

    if (request.isError || !request.data) {
        return frame(
            <>
                {back}
                <View style={{ paddingHorizontal: 20, paddingTop: 8, gap: 8 }}>
                    <Text size={20} weight="medium">Nothing to sign in to here</Text>
                    <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{SIGN_IN_NOT_YOURS} It may also have been answered already, or have stopped waiting.</Text>
                </View>
            </>,
        );
    }

    if (answer.data) {
        const said = outcomeWords(answer.data.signed_in, answer.data.working);
        return frame(
            <>
                {back}
                <View style={{ paddingHorizontal: 20, paddingTop: 8, gap: 10 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <Glyph name={answer.data.signed_in ? "lock" : "deny"} size={18} tone={color.ink2} />
                        <Text size={20} weight="medium">{said.headline}</Text>
                    </View>
                    <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{said.note}</Text>
                    <View style={{ flexDirection: "row", paddingTop: 6 }}>
                        <Button title="Done" kind="outline" onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} />
                    </View>
                </View>
            </>,
        );
    }

    const wanted = whereabouts(request.data.origin, null).host;
    const where = whereabouts(request.data.origin, page.data ?? null);
    const reason = reasonLine(request.data.reason);
    const respond = (signedIn: boolean) => answer.mutate(signedIn);

    return frame(
        <>
            <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 16, gap: 4 }}>
                <BackLink />
                {/* The host the browser is really on, as the computer reports
                    it: what somebody checks before typing a password. */}
                <View accessibilityLabel={`On ${where.host}${where.secure ? "" : ", not a secure connection"}${where.elsewhere ? `, signing in to ${wanted}` : ""}`} style={{ flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <Glyph name="lock" size={15} tone={where.secure ? color.ink : color.amber} />
                    <Text size={16} weight="medium" numberOfLines={1} style={{ flexShrink: 1 }}>{where.host}</Text>
                    {!where.secure ? <Text size={13} tone={color.amber}>not a secure connection</Text> : null}
                    {where.elsewhere ? <Text size={13} tone={color.ink3}>signing in to {wanted}</Text> : null}
                </View>
            </View>
            <View style={{ paddingHorizontal: 20, paddingTop: 2, paddingBottom: 10, gap: 4 }}>
                {reason ? <Text size={14.5} tone={color.ink} style={{ lineHeight: 20 }}>{reason}</Text> : null}
                <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{SIGN_IN_FINE}</Text>
            </View>

            <View style={{ flex: 1, marginHorizontal: 12, borderRadius: 14, overflow: "hidden", backgroundColor: glass.ground, borderWidth: 1, borderColor: color.line2 }}>
                {!source.machine ? (
                    <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
                        <Text size={14} tone={glass.quiet} style={{ textAlign: "center", lineHeight: 20 }}>{SAMPLE_SCREEN}</Text>
                    </View>
                ) : focused ? (
                    <>
                        <LiveScreen origin={request.data.origin} reopen={reopen} bar={{ done: false }} onState={setPicture} />
                        {/* Steering is not instant, and until it lands the
                            picture is a blank page: said as progress, with
                            the one handle there is, which is to steer again. */}
                        {picture === "live" && !where.arrived ? (
                            <View pointerEvents="box-none" style={{ position: "absolute", left: 0, right: 0, top: 0, alignItems: "center", paddingTop: 12 }}>
                                <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingLeft: 14, paddingRight: 6, borderRadius: 999, backgroundColor: glass.press, borderWidth: 1, borderColor: glass.line }}>
                                    <ActivityIndicator size="small" color={glass.quiet} />
                                    <Text size={13.5} tone={glass.ink}>Connecting to {wanted}…</Text>
                                    <Press label="Try again" onPress={() => setReopen((n) => n + 1)} style={{ minHeight: TOUCH - 4, justifyContent: "center", paddingHorizontal: 8 }}>
                                        <Text size={13.5} weight="medium" tone={glass.ink}>Try again</Text>
                                    </Press>
                                </View>
                            </View>
                        ) : null}
                        {picture !== "live" ? (
                            <View pointerEvents="none" style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 49, alignItems: "center", justifyContent: "center", padding: 24 }}>
                                <Text accessibilityLiveRegion="polite" size={14} tone={glass.quiet} style={{ textAlign: "center", lineHeight: 20 }}>{signInTrouble(picture)}</Text>
                            </View>
                        ) : null}
                    </>
                ) : null}
            </View>

            <View style={{ paddingHorizontal: 20, paddingTop: 10, gap: 8 }}>
                {opensInBrowser(picture) ? (
                    <Press label="Open the browser in your phone’s browser instead" onPress={() => tab.mutate()} disabled={tab.isPending} style={{ minHeight: 36, justifyContent: "center" }}>
                        <Text size={14} weight="medium">{tab.isPending ? "Opening…" : "Open the browser in your phone’s browser instead"}</Text>
                    </Press>
                ) : null}
                {tab.isError ? <Text size={13} tone={color.amber}>{problemOf(tab.error, "Couldn’t open the browser.")}</Text> : null}
                {answer.isError ? (
                    <Text accessibilityRole="alert" size={13.5} tone={color.amber} style={{ lineHeight: 19 }}>
                        {problemOf(answer.error, "That didn’t get through. It may have stopped waiting: try again, or close this and tell it in the chat.")}
                    </Text>
                ) : null}
                {/* Both answers, always. "Can't" is not a cancel: it lets the
                    run end rather than wait on somebody who has given up. */}
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title="Can’t right now" kind="outline" grow disabled={answer.isPending} onPress={() => respond(false)} />
                    <Button title="I’m signed in" grow busy={answer.isPending && answer.variables === true} disabled={answer.isPending} onPress={() => respond(true)} />
                </View>
            </View>
        </>,
    );
}
