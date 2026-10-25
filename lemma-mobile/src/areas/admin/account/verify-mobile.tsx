import { useEffect, useRef } from "react";
import { ActivityIndicator, Linking, View } from "react-native";
import { useRouter } from "expo-router";
import type { UserResponse } from "lemma-sdk";
import { Icon } from "../../../ui/icons";
import { Button, Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useDeployment } from "../access/hooks";
import { adminRoutes } from "../routes";
import { Copyable, Group, Row, Say } from "../ui";
import { useProfile, useTelegramVerification, useVerifyConfig, useWhatsAppVerification } from "./hooks";
import { clock, unknownSender, verifyBlocker } from "./verify";
import { isCompleteMobileNumber, normalizeMobileNumber } from "@web/session/mobile-number";

/** Proving a mobile number over WhatsApp, drawn twice (the web's
 *  `session/mobile-verification.tsx`): on the profile, where somebody went
 *  to sort their number out, and under a Lemma's WhatsApp number, where the
 *  point is one sentence and one button. On a phone the message is written
 *  for the person: "Open WhatsApp" opens it addressed and filled in, and
 *  sending it is the whole job. */

/** The countdown: Inter, and no live region, since it changes every second. */
function Countdown({ seconds }: { seconds: number }) {
    return (
        <View accessibilityLabel={`${Math.ceil(seconds / 60)} minutes left`} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
            <Icon name="clock" size={14} tone={color.ink3} />
            <Num size={13.5} tone={color.ink3}>{clock(seconds)}</Num>
        </View>
    );
}

function openWhatsApp(url: string): void {
    void Linking.openURL(url).catch(() => undefined);
}

/** The number in the profile's box, proved. Nothing has to be saved first:
 *  on success the server writes the number itself, from whichever phone
 *  answered, and `onVerified` hands back the person as it now stands. */
export function VerifyMobile({ number, onVerified }: { number: string; onVerified: (user: UserResponse) => void }) {
    const config = useVerifyConfig();
    const telegram = useTelegramVerification();
    const deployment = useDeployment();
    const wa = useWhatsAppVerification(onVerified);
    const typed = normalizeMobileNumber(number);
    const blocker = verifyBlocker(number);

    if (!config.data?.available) {
        if (!config.isSuccess || !isCompleteMobileNumber(typed)) return null;
        /* Telegram's way rides on a browser's own session, which the phone
           never holds: say where it is done instead. */
        if (telegram.data) return <Say>To verify it, open your profile in Lemma on the web and choose Telegram. The phone cannot do that one yet.</Say>;
        /* A local installation matches numbers nobody proved, and its
           Telegram bot asks for the contact in the chat. Anywhere else there
           is nothing to offer. */
        return deployment === "local" ? <Say>To verify it, share your contact with Lemma's Telegram bot when it asks.</Say> : null;
    }

    if (wa.transaction) {
        return (
            <View style={{ gap: 10, paddingTop: 4 }}>
                <Group label="Send this from the phone you are claiming" right={<Countdown seconds={wa.seconds} />}>
                    <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20, paddingBottom: 4 }}>Whichever number sends it becomes the number on your profile.</Text>
                    <Row label="Send to" value={wa.transaction.displayNumber} hint="Lemma's verification number, not one you chat with." />
                    <Copyable value={wa.message} label="the message" mono />
                </Group>
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title="Open WhatsApp" grow onPress={() => openWhatsApp(wa.transaction!.whatsappUrl)} />
                    <Button title="Cancel" kind="outline" grow onPress={wa.cancel} />
                </View>
                <Waiting>Lemma notices when it arrives. You can come back here after sending it.</Waiting>
            </View>
        );
    }

    return (
        <View style={{ gap: 8, paddingTop: 4 }}>
            <View style={{ flexDirection: "row" }}>
                <Button title={wa.starting ? "Preparing…" : "Verify over WhatsApp"} kind="outline" busy={wa.starting} disabled={Boolean(blocker)} onPress={() => void wa.start(typed)} />
            </View>
            {blocker ? <Say>{blocker}</Say> : null}
            {wa.error ? <Say tone="bad">{wa.error}</Say> : null}
        </View>
    );
}

function Waiting({ children }: { children: string }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <ActivityIndicator size="small" color={color.ink3} />
            <Text size={13.5} tone={color.ink3} style={{ flex: 1, lineHeight: 18 }}>{children}</Text>
        </View>
    );
}

/** Under a Lemma's WhatsApp number: whether the person who connected it will
 *  be known when they write to it (the web's `KnownSender`).
 *
 *  A WhatsApp message carries the sender's number and nothing else, so a
 *  member whose profile has none writes to the number they were just given
 *  and is answered with a sign-up link. Said here, rather than found out
 *  there. A number on the profile is enough, proved or not, so this asks
 *  nothing of the people who have one. The code is minted on arrival, once,
 *  and never again on its own after a failure. */
export function KnownSender() {
    const profile = useProfile();
    const config = useVerifyConfig();
    const router = useRouter();
    const wa = useWhatsAppVerification(() => undefined);
    const unknown = unknownSender(profile.data);
    const canVerify = config.data?.available === true;

    const minted = useRef(false);
    const start = wa.start;
    useEffect(() => {
        if (minted.current || !profile.data || !unknown || !canVerify) return;
        minted.current = true;
        void start();
    }, [canVerify, unknown, start, profile.data]);

    if (!profile.data || !unknown || !config.isSuccess) return null;

    if (!canVerify) {
        return (
            <Group label="WhatsApp will not know it is you">
                <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>
                    Lemma knows a WhatsApp message is yours by its number, and your profile has none. Add it to your profile, and this number answers you instead of sending a sign-up link.
                </Text>
                <Press role="link" label="Open your profile" onPress={() => router.push(adminRoutes.section("profile") as never)} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
                    <Text size={15} weight="medium">Open your profile</Text>
                </Press>
            </Group>
        );
    }

    if (!wa.transaction) {
        return (
            <Group label="WhatsApp will not know it is you yet">
                {wa.error ? (
                    <View style={{ gap: 8 }}>
                        <Say tone="bad">{wa.error}</Say>
                        <View style={{ flexDirection: "row" }}>
                            <Button title={wa.starting ? "Preparing…" : "Try again"} kind="outline" busy={wa.starting} onPress={() => void wa.start()} />
                        </View>
                    </View>
                ) : (
                    <Waiting>Preparing a message that tells Lemma this phone is yours…</Waiting>
                )}
            </Group>
        );
    }

    return (
        <View style={{ gap: 10 }}>
            <Group label="Send this once, from your phone" right={<Countdown seconds={wa.seconds} />}>
                <Copyable value={wa.message} label="the message" mono />
                <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19, paddingTop: 6 }}>
                    It goes to {wa.transaction.displayNumber}, Lemma's verification number, not one you chat with. Whichever phone sends it becomes the number you are known by here.
                </Text>
            </Group>
            <View style={{ flexDirection: "row" }}>
                <Button title="Open WhatsApp" onPress={() => openWhatsApp(wa.transaction!.whatsappUrl)} />
            </View>
        </View>
    );
}
